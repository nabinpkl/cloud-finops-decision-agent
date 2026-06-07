"""Request body guards for public POST endpoints."""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app_config import settings


class RequestBodySizeLimitMiddleware:
    """Reject oversized public request bodies before app work begins."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit = _limit_for_path(scope)
        if limit is None:
            await self.app(scope, receive, send)
            return

        declared_size = _content_length(scope)
        if declared_size is not None and declared_size > limit:
            await _too_large(scope, send, limit)
            return

        received = 0

        async def guarded_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise RequestBodyTooLarge
            return message

        try:
            await self.app(scope, guarded_receive, send)
        except RequestBodyTooLarge:
            await _too_large(scope, send, limit)


class RequestBodyTooLarge(Exception):
    pass


async def _too_large(scope: Scope, send: Send, limit: int) -> None:
    path = scope.get("path")
    error = "assistant_body_too_large" if path == "/assistant" else "public_body_too_large"
    response = JSONResponse(
        {"error": error, "max_bytes": limit},
        status_code=413,
    )
    await response(scope, _empty_receive, send)


async def _empty_receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


def _content_length(scope: Scope) -> int | None:
    for raw_name, raw_value in scope.get("headers", []):
        if raw_name.lower() == b"content-length":
            try:
                return int(raw_value)
            except ValueError:
                return None
    return None


def _limit_for_path(scope: Scope) -> int | None:
    if scope["type"] != "http":
        return None
    path = scope.get("path")
    if path == "/assistant":
        return settings.assistant_max_body_bytes
    if path == "/compare":
        return settings.public_max_body_bytes
    return None
