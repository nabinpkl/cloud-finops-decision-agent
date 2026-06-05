"""Request body guard for the public assistant endpoint."""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app_config import settings


class AssistantBodySizeLimitMiddleware:
    """Reject oversized assistant bodies before Pydantic/model work begins."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") != "/assistant":
            await self.app(scope, receive, send)
            return

        limit = settings.assistant_max_body_bytes
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
                    raise ClientBodyTooLarge
            return message

        try:
            await self.app(scope, guarded_receive, send)
        except ClientBodyTooLarge:
            await _too_large(scope, send, limit)


class ClientBodyTooLarge(Exception):
    pass


async def _too_large(scope: Scope, send: Send, limit: int) -> None:
    response = JSONResponse(
        {"error": "assistant_body_too_large", "max_bytes": limit},
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
