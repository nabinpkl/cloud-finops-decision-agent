"""Content-capture gates for error telemetry."""

from __future__ import annotations

import json

from opentelemetry.trace import Span, Status, StatusCode

from app_config import settings

REDACTED_ERROR_DESCRIPTION = "error details redacted"


def record_exception(span: Span, exc: Exception) -> None:
    if settings.otel_capture_content:
        span.record_exception(exc)
        return
    span.set_attribute("finops.error.redacted", True)
    span.set_attribute("finops.error.type", _exception_type(exc))


def set_error_status(span: Span, exc: Exception) -> None:
    description = str(exc) if settings.otel_capture_content else REDACTED_ERROR_DESCRIPTION
    span.set_status(Status(StatusCode.ERROR, description))


def set_sdk_error(span: Span, error: dict[str, object]) -> None:
    if settings.otel_capture_content:
        message = error.get("message")
        span.set_status(Status(StatusCode.ERROR, str(message or "")))
        data = error.get("data")
        if data:
            span.set_attribute("finops.error.data", json.dumps(data, default=str))
        return
    span.set_status(Status(StatusCode.ERROR, REDACTED_ERROR_DESCRIPTION))
    span.set_attribute("finops.error.redacted", True)
    if error.get("message"):
        span.set_attribute("finops.error.message_redacted", True)
    if error.get("data"):
        span.set_attribute("finops.error.data_redacted", True)


def _exception_type(exc: Exception) -> str:
    return f"{type(exc).__module__}.{type(exc).__qualname__}"
