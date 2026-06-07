from __future__ import annotations

from typing import Any

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from api.observability.redaction import record_exception, set_error_status, set_sdk_error
from app_config import settings


def _attrs(span: ReadableSpan) -> dict[str, Any]:
    assert span.attributes is not None
    return dict(span.attributes)


def _span_with_exporter() -> tuple[TracerProvider, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def test_exception_details_redacted_when_content_capture_off(monkeypatch):
    monkeypatch.setattr(settings, "otel_capture_content", False)
    provider, exporter = _span_with_exporter()
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("x") as span:
        exc = RuntimeError("SECRET_PROVIDER_PAYLOAD")
        record_exception(span, exc)
        set_error_status(span, exc)

    provider.shutdown()
    finished = exporter.get_finished_spans()[0]
    attrs = _attrs(finished)

    assert attrs["finops.error.redacted"] is True
    assert attrs["finops.error.type"] == "builtins.RuntimeError"
    assert "SECRET_PROVIDER_PAYLOAD" not in str(finished.status.description)
    assert all("SECRET_PROVIDER_PAYLOAD" not in str(event.attributes) for event in finished.events)


def test_sdk_error_data_redacted_when_content_capture_off(monkeypatch):
    monkeypatch.setattr(settings, "otel_capture_content", False)
    provider, exporter = _span_with_exporter()
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("x") as span:
        set_sdk_error(
            span,
            {
                "message": "SECRET_PROVIDER_MESSAGE",
                "data": {"body": "SECRET_PROVIDER_BODY"},
            },
        )

    provider.shutdown()
    finished = exporter.get_finished_spans()[0]
    attrs = _attrs(finished)

    assert attrs["finops.error.redacted"] is True
    assert attrs["finops.error.message_redacted"] is True
    assert attrs["finops.error.data_redacted"] is True
    assert "finops.error.data" not in attrs
    assert "SECRET_PROVIDER" not in str(finished.status.description)


def test_sdk_error_data_recorded_when_content_capture_on(monkeypatch):
    monkeypatch.setattr(settings, "otel_capture_content", True)
    provider, exporter = _span_with_exporter()
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("x") as span:
        set_sdk_error(
            span,
            {
                "message": "provider failed",
                "data": {"body": "visible when enabled"},
            },
        )

    provider.shutdown()
    attrs = _attrs(exporter.get_finished_spans()[0])

    assert attrs["finops.error.data"] == '{"body": "visible when enabled"}'
