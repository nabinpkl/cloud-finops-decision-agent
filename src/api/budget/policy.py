"""Budget cap checks for the /assistant model surface."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from api.budget.identity import utc_date_str
from api.budget.models import BudgetBlock
from api.budget.store import _LOCK, conn, read_session_usage
from api.config import settings


def seconds_to_next_utc_midnight() -> int:
    now = datetime.now(timezone.utc)
    seconds = 86400 - (now.hour * 3600 + now.minute * 60 + now.second)
    return max(seconds, 1)


def check_global_daily() -> BudgetBlock | None:
    if not settings.budget_enabled:
        return None
    today = utc_date_str()
    with _LOCK:
        row = conn().execute(
            "SELECT tokens_input, tokens_output FROM global_daily WHERE utc_date=?",
            (today,),
        ).fetchone()
    used = (int(row[0]) + int(row[1])) if row else 0
    if used >= settings.global_daily_token_cap:
        return BudgetBlock(
            reason="global_daily",
            http_status=503,
            retry_after_seconds=seconds_to_next_utc_midnight(),
        )
    return None


def check_client_rate(hashed_id: str) -> BudgetBlock | None:
    """Increment request counter and enforce request/token rate caps."""
    if not settings.budget_enabled:
        return None
    now = int(time.time())
    with _LOCK:
        db = conn()
        row = db.execute(
            "SELECT minute_window_start, minute_requests, hour_window_start, hour_tokens "
            "FROM client_window WHERE hashed_id=?",
            (hashed_id,),
        ).fetchone()
        if row is None:
            minute_start, minute_requests = now, 0
            hour_start, hour_tokens = now, 0
        else:
            minute_start, minute_requests = int(row[0]), int(row[1])
            hour_start, hour_tokens = int(row[2]), int(row[3])
            if now - minute_start >= 60:
                minute_start, minute_requests = now, 0
            if now - hour_start >= 3600:
                hour_start, hour_tokens = now, 0

        if hour_tokens >= settings.client_rate_tokens_per_hour:
            return BudgetBlock(
                reason="client_token_rate",
                http_status=429,
                retry_after_seconds=max(3600 - (now - hour_start), 1),
            )
        if minute_requests >= settings.client_rate_requests_per_minute:
            return BudgetBlock(
                reason="client_request_rate",
                http_status=429,
                retry_after_seconds=max(60 - (now - minute_start), 1),
            )

        db.execute(
            """
            INSERT INTO client_window (hashed_id, minute_window_start, minute_requests, hour_window_start, hour_tokens)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(hashed_id) DO UPDATE SET
                minute_window_start = excluded.minute_window_start,
                minute_requests     = excluded.minute_requests,
                hour_window_start   = excluded.hour_window_start,
                hour_tokens         = excluded.hour_tokens
            """,
            (hashed_id, minute_start, minute_requests + 1, hour_start, hour_tokens),
        )
        return None


def check_session_cap(session_id: str) -> BudgetBlock | None:
    """Return a block if the cumulative session token total is at the cap."""
    if not settings.budget_enabled:
        return None
    usage = read_session_usage(session_id)
    if usage.total >= settings.session_token_cap:
        return BudgetBlock(
            reason="session",
            http_status=200,
            retry_after_seconds=0,
        )
    return None
