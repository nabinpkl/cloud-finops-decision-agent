"""Server-trusted budget enforcement for the public /assistant endpoint
(ADR-0011).

Three concerns live here, kept together so the wiring reads end to end:

1. A SQLite store at `settings.budget_db_path` with three tables
   (`global_daily`, `client_window`, `session`). Schema is created on init
   via `CREATE TABLE IF NOT EXISTS`; no migrations in v0.

2. Identity primitives: `hashed_client_id(ip)` returns an HMAC-SHA256 over
   the IP keyed by (process secret + UTC date), so the digest rotates at
   UTC midnight and the raw IP is never persisted. `new_session_id()`
   returns the value the backend sets in the `finops_session_id` cookie.

3. Check + record functions. The check functions return `BudgetBlock`
   (with `http_status` and `retry_after_seconds`) when a cap is hit, or
   `None` when the request is allowed; `record_usage` is the single
   transactional write that increments the session row, the client window,
   and the global daily counter.

The module is sync (regular `def`). Callers in async paths invoke these
directly; the SQLite operations are sub-millisecond against the small v0
schema and blocking the event loop for that is acceptable until QPS makes
it not. Move to `asyncio.to_thread` or `aiosqlite` when scale demands.
"""

from __future__ import annotations

import hmac
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import ClassVar

from api.config import settings
from api.observability import PRICE_TABLE
from gates._shared import PROJECT_ROOT

_LOCK = threading.Lock()


# ---------- data shapes ----------


@dataclass(frozen=True)
class SessionUsage:
    session_id: str
    input_tokens:  int
    output_tokens: int

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class BudgetBlock:
    """Returned from `check_*` functions when a cap blocks the request.

    `http_status` and `retry_after_seconds` are what the middleware/handler
    forwards to the client.
    """

    reason: str               # "global_daily" | "client_request_rate" | "client_token_rate" | "session"
    http_status: int          # 503 (global), 429 (client), or surfaced as in-thread message (session)
    retry_after_seconds: int  # 0 if no retry helps (session cap)


# ---------- schema ----------


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


# ---------- init ----------


class _Init:
    """Module-level latch + connection handle. Matches the pattern in
    `api/observability.py` so calling `init_budgets()` twice (reload) is a
    no-op and tests can reset by patching `done`/`conn`."""

    done: ClassVar[bool] = False
    conn: ClassVar[sqlite3.Connection | None] = None


def _resolve_db_path(raw: str) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else PROJECT_ROOT / p


def init_budgets() -> None:
    """Open the SQLite store, bootstrap the schema, set the module handle.
    Idempotent: a second call while initialized is a no-op."""
    if _Init.done or not settings.budget_enabled:
        return
    db_path = _resolve_db_path(settings.budget_db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI may dispatch to a threadpool; the
    # module-level `_LOCK` serializes mutations across threads.
    conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    _Init.conn = conn
    _Init.done = True


def _conn() -> sqlite3.Connection:
    if _Init.conn is None:
        init_budgets()
    assert _Init.conn is not None
    return _Init.conn


# ---------- identity ----------


def _utc_date_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def hashed_client_id(ip: str) -> str:
    """HMAC-SHA256(salt + utc_date, ip), truncated. Rotates at UTC midnight
    by virtue of the date being part of the key, so yesterday's digests
    cannot be correlated to today's. Returns 32 hex chars (128 bits)."""
    salt = settings.budget_ip_hash_salt_secret.encode("utf-8")
    key  = salt + _utc_date_str().encode("utf-8")
    return hmac.new(key, ip.encode("utf-8"), sha256).hexdigest()[:32]


def new_session_id() -> str:
    """Opaque random session id for the `finops_session_id` cookie."""
    return secrets.token_urlsafe(24)


def session_id_fingerprint(session_id: str) -> str:
    """First 8 chars of sha256(session_id), for grouping traces by session
    without storing the raw id on a span."""
    return sha256(session_id.encode("utf-8")).hexdigest()[:8]


# ---------- read / record ----------


def read_session_usage(session_id: str) -> SessionUsage:
    """Return current session usage; creates the row on first read so the
    server-issued cookie has a server-side counter from the start."""
    now = int(time.time())
    with _LOCK:
        conn = _conn()
        row = conn.execute(
            "SELECT tokens_input, tokens_output FROM session WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO session (session_id, created_at, last_seen) VALUES (?, ?, ?)",
                (session_id, now, now),
            )
            return SessionUsage(session_id=session_id, input_tokens=0, output_tokens=0)
        conn.execute(
            "UPDATE session SET last_seen=? WHERE session_id=?",
            (now, session_id),
        )
        return SessionUsage(
            session_id=session_id, input_tokens=int(row[0]), output_tokens=int(row[1])
        )


def record_usage(
    session_id: str,
    hashed_id: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Increment the three counters atomically. Run from
    `transport.py`'s `finally` so a partially-streamed turn still pays for
    what it consumed."""
    if input_tokens == 0 and output_tokens == 0:
        return
    now = int(time.time())
    today = _utc_date_str()
    with _LOCK:
        conn = _conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
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
            conn.execute(
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
            # Hour-token window is updated here (request-rate is updated in
            # check_client_rate, which fires per HTTP request regardless of
            # whether the model was called).
            row = conn.execute(
                "SELECT hour_window_start, hour_tokens FROM client_window WHERE hashed_id=?",
                (hashed_id,),
            ).fetchone()
            tokens = input_tokens + output_tokens
            if row is None:
                conn.execute(
                    """
                    INSERT INTO client_window (hashed_id, minute_window_start, minute_requests, hour_window_start, hour_tokens)
                    VALUES (?, ?, 0, ?, ?)
                    """,
                    (hashed_id, now, now, tokens),
                )
            else:
                hw_start, hw_tokens = int(row[0]), int(row[1])
                if now - hw_start >= 3600:
                    hw_start, hw_tokens = now, 0
                conn.execute(
                    "UPDATE client_window SET hour_window_start=?, hour_tokens=? WHERE hashed_id=?",
                    (hw_start, hw_tokens + tokens, hashed_id),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


# ---------- checks ----------


def _seconds_to_next_utc_midnight() -> int:
    now = datetime.now(timezone.utc)
    secs = 86400 - (now.hour * 3600 + now.minute * 60 + now.second)
    return max(secs, 1)


def check_global_daily() -> BudgetBlock | None:
    if not settings.budget_enabled:
        return None
    today = _utc_date_str()
    with _LOCK:
        row = _conn().execute(
            "SELECT tokens_input, tokens_output FROM global_daily WHERE utc_date=?",
            (today,),
        ).fetchone()
    used = (int(row[0]) + int(row[1])) if row else 0
    if used >= settings.global_daily_token_cap:
        return BudgetBlock(
            reason="global_daily",
            http_status=503,
            retry_after_seconds=_seconds_to_next_utc_midnight(),
        )
    return None


def check_client_rate(hashed_id: str) -> BudgetBlock | None:
    """Increment the per-client request counter; return a BudgetBlock if
    either the per-minute request cap or the per-hour token cap is
    exceeded. Tokens are not incremented here (they are not known until
    after the model call); they accumulate via `record_usage`."""
    if not settings.budget_enabled:
        return None
    now = int(time.time())
    with _LOCK:
        conn = _conn()
        row = conn.execute(
            "SELECT minute_window_start, minute_requests, hour_window_start, hour_tokens "
            "FROM client_window WHERE hashed_id=?",
            (hashed_id,),
        ).fetchone()
        if row is None:
            mw_start, mw_req = now, 0
            hw_start, hw_tokens = now, 0
        else:
            mw_start, mw_req     = int(row[0]), int(row[1])
            hw_start, hw_tokens  = int(row[2]), int(row[3])
            if now - mw_start >= 60:
                mw_start, mw_req     = now, 0
            if now - hw_start >= 3600:
                hw_start, hw_tokens  = now, 0

        if hw_tokens >= settings.client_rate_tokens_per_hour:
            return BudgetBlock(
                reason="client_token_rate",
                http_status=429,
                retry_after_seconds=max(3600 - (now - hw_start), 1),
            )
        if mw_req >= settings.client_rate_requests_per_minute:
            return BudgetBlock(
                reason="client_request_rate",
                http_status=429,
                retry_after_seconds=max(60 - (now - mw_start), 1),
            )

        conn.execute(
            """
            INSERT INTO client_window (hashed_id, minute_window_start, minute_requests, hour_window_start, hour_tokens)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(hashed_id) DO UPDATE SET
                minute_window_start = excluded.minute_window_start,
                minute_requests     = excluded.minute_requests,
                hour_window_start   = excluded.hour_window_start,
                hour_tokens         = excluded.hour_tokens
            """,
            (hashed_id, mw_start, mw_req + 1, hw_start, hw_tokens),
        )
        return None


def check_session_cap(session_id: str) -> BudgetBlock | None:
    """Return BudgetBlock if the cumulative session token total is at or
    past the cap. No retry: the user must start a new conversation."""
    if not settings.budget_enabled:
        return None
    usage = read_session_usage(session_id)
    if usage.total >= settings.session_token_cap:
        return BudgetBlock(
            reason="session",
            http_status=200,           # delivered as in-thread message, not an HTTP error
            retry_after_seconds=0,
        )
    return None


# ---------- cost view ----------


def tokens_to_usd_view(tokens: int, model_name: str) -> float:
    """Render tokens as approximate USD using the existing PRICE_TABLE.
    Average of input and output rate; the cap is in tokens regardless.
    Returns 0.0 for unknown models so operator logs degrade gracefully."""
    prices = PRICE_TABLE.get(model_name)
    if not prices:
        return 0.0
    avg_per_1m = (prices[0] + prices[1]) / 2.0
    return (tokens * avg_per_1m) / 1_000_000
