"""SQLite persistence for budget enforcement."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import ClassVar, Protocol

from api.budget.identity import utc_date_str
from api.budget.models import SessionUsage
from app_config import settings
from project_paths import PROJECT_ROOT

_LOCK = threading.Lock()


class UsageLike(Protocol):
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cached_input_tokens: int

    @property
    def total(self) -> int:
        ...

_SCHEMA = """
CREATE TABLE IF NOT EXISTS global_daily (
    utc_date       TEXT    PRIMARY KEY,
    tokens_input   INTEGER NOT NULL DEFAULT 0,
    tokens_output  INTEGER NOT NULL DEFAULT 0,
    tokens_total   INTEGER NOT NULL DEFAULT 0,
    tokens_reasoning INTEGER NOT NULL DEFAULT 0,
    tokens_cached_input INTEGER NOT NULL DEFAULT 0,
    requests       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS client_window (
    hashed_id            TEXT    PRIMARY KEY,
    minute_window_start  INTEGER NOT NULL,
    minute_requests      INTEGER NOT NULL DEFAULT 0,
    hour_window_start    INTEGER NOT NULL,
    hour_tokens          INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS public_route_window (
    hashed_id            TEXT    NOT NULL,
    route                TEXT    NOT NULL,
    minute_window_start  INTEGER NOT NULL,
    minute_requests      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (hashed_id, route)
);

CREATE TABLE IF NOT EXISTS session (
    session_id     TEXT    PRIMARY KEY,
    created_at     INTEGER NOT NULL,
    last_seen      INTEGER NOT NULL,
    tokens_input   INTEGER NOT NULL DEFAULT 0,
    tokens_output  INTEGER NOT NULL DEFAULT 0,
    tokens_total   INTEGER NOT NULL DEFAULT 0,
    tokens_reasoning INTEGER NOT NULL DEFAULT 0,
    tokens_cached_input INTEGER NOT NULL DEFAULT 0
);
"""

_MIGRATED_COLUMNS: dict[str, dict[str, str]] = {
    "global_daily": {
        "tokens_total": "INTEGER NOT NULL DEFAULT 0",
        "tokens_reasoning": "INTEGER NOT NULL DEFAULT 0",
        "tokens_cached_input": "INTEGER NOT NULL DEFAULT 0",
    },
    "session": {
        "tokens_total": "INTEGER NOT NULL DEFAULT 0",
        "tokens_reasoning": "INTEGER NOT NULL DEFAULT 0",
        "tokens_cached_input": "INTEGER NOT NULL DEFAULT 0",
    },
}


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
    if _Init.done:
        return
    db_path = resolve_db_path(settings.budget_db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    _migrate_schema(conn)
    _Init.conn = conn
    _Init.done = True


def _migrate_schema(db: sqlite3.Connection) -> None:
    for table, columns in _MIGRATED_COLUMNS.items():
        existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        for column, definition in columns.items():
            if column not in existing:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


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
            """
            SELECT tokens_input, tokens_output, tokens_total, tokens_reasoning,
                   tokens_cached_input
            FROM session WHERE session_id=?
            """,
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
            total_tokens=int(row[2]),
            reasoning_tokens=int(row[3]),
            cached_input_tokens=int(row[4]),
        )


def record_usage(
    session_id: str,
    hashed_id: str,
    usage: UsageLike | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
    reasoning_tokens: int = 0,
    cached_input_tokens: int = 0,
) -> None:
    """Increment session, global, and client token counters atomically."""
    if usage is not None:
        input_tokens = usage.input_tokens
        output_tokens = usage.output_tokens
        total_tokens = usage.total
        reasoning_tokens = usage.reasoning_tokens
        cached_input_tokens = usage.cached_input_tokens
    else:
        total_tokens = total_tokens or (input_tokens + output_tokens)
    if total_tokens == 0:
        return
    now = int(time.time())
    today = utc_date_str()
    with _LOCK:
        db = conn()
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute(
                """
                INSERT INTO session (
                    session_id, created_at, last_seen, tokens_input, tokens_output,
                    tokens_total, tokens_reasoning, tokens_cached_input
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    last_seen           = excluded.last_seen,
                    tokens_input        = session.tokens_input        + excluded.tokens_input,
                    tokens_output       = session.tokens_output       + excluded.tokens_output,
                    tokens_total        = session.tokens_total        + excluded.tokens_total,
                    tokens_reasoning    = session.tokens_reasoning    + excluded.tokens_reasoning,
                    tokens_cached_input = session.tokens_cached_input + excluded.tokens_cached_input
                """,
                (
                    session_id,
                    now,
                    now,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    reasoning_tokens,
                    cached_input_tokens,
                ),
            )
            db.execute(
                """
                INSERT INTO global_daily (
                    utc_date, tokens_input, tokens_output, tokens_total,
                    tokens_reasoning, tokens_cached_input, requests
                )
                VALUES (?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(utc_date) DO UPDATE SET
                    tokens_input        = global_daily.tokens_input        + excluded.tokens_input,
                    tokens_output       = global_daily.tokens_output       + excluded.tokens_output,
                    tokens_total        = global_daily.tokens_total        + excluded.tokens_total,
                    tokens_reasoning    = global_daily.tokens_reasoning    + excluded.tokens_reasoning,
                    tokens_cached_input = global_daily.tokens_cached_input + excluded.tokens_cached_input,
                    requests            = global_daily.requests            + 1
                """,
                (
                    today,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    reasoning_tokens,
                    cached_input_tokens,
                ),
            )
            row = db.execute(
                "SELECT hour_window_start, hour_tokens FROM client_window WHERE hashed_id=?",
                (hashed_id,),
            ).fetchone()
            if row is None:
                db.execute(
                    """
                    INSERT INTO client_window (hashed_id, minute_window_start, minute_requests, hour_window_start, hour_tokens)
                    VALUES (?, ?, 0, ?, ?)
                    """,
                    (hashed_id, now, now, total_tokens),
                )
            else:
                hour_start, hour_tokens = int(row[0]), int(row[1])
                if now - hour_start >= 3600:
                    hour_start, hour_tokens = now, 0
                db.execute(
                    "UPDATE client_window SET hour_window_start=?, hour_tokens=? WHERE hashed_id=?",
                    (hour_start, hour_tokens + total_tokens, hashed_id),
                )
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
