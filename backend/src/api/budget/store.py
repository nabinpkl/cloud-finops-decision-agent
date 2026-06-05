"""SQLite persistence for budget enforcement."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import ClassVar

from api.budget.identity import utc_date_str
from api.budget.models import SessionUsage
from app_config import settings
from ingest._shared import PROJECT_ROOT

_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS global_daily (
    utc_date       TEXT    PRIMARY KEY,
    tokens_input   INTEGER NOT NULL DEFAULT 0,
    tokens_output  INTEGER NOT NULL DEFAULT 0,
    requests       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS client_window (
    hashed_id            TEXT    PRIMARY KEY,
    minute_window_start  INTEGER NOT NULL,
    minute_requests      INTEGER NOT NULL DEFAULT 0,
    hour_window_start    INTEGER NOT NULL,
    hour_tokens          INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS session (
    session_id     TEXT    PRIMARY KEY,
    created_at     INTEGER NOT NULL,
    last_seen      INTEGER NOT NULL,
    tokens_input   INTEGER NOT NULL DEFAULT 0,
    tokens_output  INTEGER NOT NULL DEFAULT 0
);
"""


class _Init:
    """Module-level latch + connection handle.

    Tests reset this directly so the SQLite store can point at a temp path.
    """

    done: ClassVar[bool] = False
    conn: ClassVar[sqlite3.Connection | None] = None


def resolve_db_path(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else PROJECT_ROOT / path


def init_budgets() -> None:
    """Open the SQLite store and bootstrap schema. Idempotent."""
    if _Init.done or not settings.budget_enabled:
        return
    db_path = resolve_db_path(settings.budget_db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    _Init.conn = conn
    _Init.done = True


def conn() -> sqlite3.Connection:
    if _Init.conn is None:
        init_budgets()
    assert _Init.conn is not None
    return _Init.conn


def read_session_usage(session_id: str) -> SessionUsage:
    """Return current session usage, creating the row on first read."""
    now = int(time.time())
    with _LOCK:
        db = conn()
        row = db.execute(
            "SELECT tokens_input, tokens_output FROM session WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if row is None:
            db.execute(
                "INSERT INTO session (session_id, created_at, last_seen) VALUES (?, ?, ?)",
                (session_id, now, now),
            )
            return SessionUsage(session_id=session_id, input_tokens=0, output_tokens=0)
        db.execute(
            "UPDATE session SET last_seen=? WHERE session_id=?",
            (now, session_id),
        )
        return SessionUsage(
            session_id=session_id,
            input_tokens=int(row[0]),
            output_tokens=int(row[1]),
        )


def record_usage(
    session_id: str,
    hashed_id: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Increment session, global, and client token counters atomically."""
    if input_tokens == 0 and output_tokens == 0:
        return
    now = int(time.time())
    today = utc_date_str()
    with _LOCK:
        db = conn()
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute(
                """
                INSERT INTO session (session_id, created_at, last_seen, tokens_input, tokens_output)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    last_seen     = excluded.last_seen,
                    tokens_input  = session.tokens_input  + excluded.tokens_input,
                    tokens_output = session.tokens_output + excluded.tokens_output
                """,
                (session_id, now, now, input_tokens, output_tokens),
            )
            db.execute(
                """
                INSERT INTO global_daily (utc_date, tokens_input, tokens_output, requests)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(utc_date) DO UPDATE SET
                    tokens_input  = global_daily.tokens_input  + excluded.tokens_input,
                    tokens_output = global_daily.tokens_output + excluded.tokens_output,
                    requests      = global_daily.requests      + 1
                """,
                (today, input_tokens, output_tokens),
            )
            row = db.execute(
                "SELECT hour_window_start, hour_tokens FROM client_window WHERE hashed_id=?",
                (hashed_id,),
            ).fetchone()
            tokens = input_tokens + output_tokens
            if row is None:
                db.execute(
                    """
                    INSERT INTO client_window (hashed_id, minute_window_start, minute_requests, hour_window_start, hour_tokens)
                    VALUES (?, ?, 0, ?, ?)
                    """,
                    (hashed_id, now, now, tokens),
                )
            else:
                hour_start, hour_tokens = int(row[0]), int(row[1])
                if now - hour_start >= 3600:
                    hour_start, hour_tokens = now, 0
                db.execute(
                    "UPDATE client_window SET hour_window_start=?, hour_tokens=? WHERE hashed_id=?",
                    (hour_start, hour_tokens + tokens, hashed_id),
                )
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
