"""FastAPI middleware for anonymous public-route throttles and model budgets.

`POST /assistant` is the wallet surface, so it gets global daily token and
per-client token/request caps. Deterministic routes (`/compare`, `/lookup`,
`/citation/excerpt`) do not spend model tokens, but they still touch local
indexes or snapshots and get a cheap request-rate cap.

The client id used for rate limiting is the salted-hash from
`api/budget_identity.py`; this middleware never sees a raw IP after the
one-line HMAC, and never persists one.
"""

from __future__ import annotations

import ipaddress
from typing import Awaitable, Callable

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from api.budget.identity import hashed_client_id
from api.budget.models import BudgetBlock
from api.budget.policy import (
    check_client_rate,
    check_global_daily,
    check_public_route_rate,
)
from app_config import settings


_ASSISTANT_PATH = "/assistant"
_PUBLIC_RATE_PATHS = {"/compare", "/lookup", "/citation/excerpt"}


def _client_ip(request: Request) -> str:
    """Pick the client IP, honoring `trusted_proxy_count` hops of
    `X-Forwarded-For` only from configured trusted proxy CIDRs."""
    client = request.client
    peer_ip = client.host if client else "0.0.0.0"
    if settings.trusted_proxy_count > 0 and _peer_is_trusted(peer_ip):
        fwd = request.headers.get("x-forwarded-for", "")
        chain = _validated_forwarded_chain(fwd)
        if chain:
            # The right-most `trusted_proxy_count` entries are our own
            # proxies; the value left of them is the closest untrusted
            # hop, which is what we attribute the request to.
            index = max(len(chain) - settings.trusted_proxy_count - 1, 0)
            return chain[index]
    return peer_ip


def _validated_forwarded_chain(header: str) -> list[str]:
    chain = [ip.strip() for ip in header.split(",") if ip.strip()]
    if not chain:
        return []
    try:
        for ip in chain:
            ipaddress.ip_address(ip)
    except ValueError:
        return []
    return chain


def _peer_is_trusted(peer_ip: str) -> bool:
    try:
        ip = ipaddress.ip_address(peer_ip)
    except ValueError:
        return False
    return any(
        ip in ipaddress.ip_network(cidr, strict=False)
        for cidr in settings.trusted_proxy_cidrs
    )


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
        path = request.url.path
        if path != _ASSISTANT_PATH and path not in _PUBLIC_RATE_PATHS:
            return await call_next(request)

        ip = _client_ip(request)
        hashed_id = hashed_client_id(ip)

        if path in _PUBLIC_RATE_PATHS:
            cap = (
                settings.excerpt_rate_requests_per_minute
                if path == "/citation/excerpt"
                else settings.public_rate_requests_per_minute
            )
            block = check_public_route_rate(
                hashed_id,
                route=path,
                requests_per_minute=cap,
            )
            if block is not None:
                return _block_response(block)
            return await call_next(request)

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
