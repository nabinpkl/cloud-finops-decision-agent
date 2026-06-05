"""FastAPI middleware that enforces the global daily token cap and the
per-client request-rate cap before any agent work runs (ADR-0011, seam
[2]).

Scope: this middleware fires for `POST /assistant` only. `/health`,
`/compare`, `/lookup`, and `/citation/excerpt` are deterministic and
model-free; they share the process but are not the wallet surface, so
they pass through. The session-cap check (seam [3]) lives in
`api/transport.py` because it needs the cookie-derived session id, which
the route handler can read more naturally than a generic middleware.

The client id used for rate limiting is the salted-hash from
`api/budget_identity.py`; this middleware never sees a raw IP after the
one-line HMAC, and never persists one.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from api.budget_identity import hashed_client_id
from api.budget_models import BudgetBlock
from api.budget_policy import check_client_rate, check_global_daily
from api.config import settings


_GUARDED_PATH = "/assistant"


def _client_ip(request: Request) -> str:
    """Pick the client IP, honoring `trusted_proxy_count` hops of
    `X-Forwarded-For`. With the v0 default of 0, this is just
    `request.client.host`."""
    if settings.trusted_proxy_count > 0:
        fwd = request.headers.get("x-forwarded-for", "")
        chain = [ip.strip() for ip in fwd.split(",") if ip.strip()]
        if chain:
            # The right-most `trusted_proxy_count` entries are our own
            # proxies; the value left of them is the closest untrusted
            # hop, which is what we attribute the request to.
            index = max(len(chain) - settings.trusted_proxy_count - 1, 0)
            return chain[index]
    client = request.client
    return client.host if client else "0.0.0.0"


def _block_response(block: BudgetBlock) -> JSONResponse:
    return JSONResponse(
        status_code=block.http_status,
        content={"error": block.reason, "retry_after": block.retry_after_seconds},
        headers={"Retry-After": str(block.retry_after_seconds)},
    )


class BudgetMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not settings.budget_enabled or request.url.path != _GUARDED_PATH:
            return await call_next(request)

        ip = _client_ip(request)
        hashed_id = hashed_client_id(ip)

        block = check_global_daily()
        if block is not None:
            return _block_response(block)

        block = check_client_rate(hashed_id)
        if block is not None:
            return _block_response(block)

        # Handed to the route handler so it can pass to record_usage in
        # the post-run finally block without re-deriving from the IP.
        request.state.hashed_client_id = hashed_id

        return await call_next(request)
