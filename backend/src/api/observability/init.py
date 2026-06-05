"""Observability initialization wiring."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from fastapi import FastAPI
from opentelemetry import trace as otel_trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from app_config import settings
from api.observability.jsonl_exporter import JsonlSpanExporter
from api.observability.provider import infer_provider_name
from project_paths import PROJECT_ROOT

TRACER_NAME = "cloud-finops-decision-agent"


class _Init:
    done: ClassVar[bool] = False
    tracer: ClassVar[otel_trace.Tracer | None] = None


def get_tracer() -> otel_trace.Tracer:
    if _Init.tracer is not None:
        return _Init.tracer
    return otel_trace.get_tracer(TRACER_NAME)


def init_observability(app: FastAPI) -> None:
    if _Init.done:
        return

    resource = Resource.create({"service.name": TRACER_NAME})
    provider = TracerProvider(resource=resource)

    jsonl_path = _resolve_jsonl_path(settings.otel_jsonl_path)
    provider.add_span_processor(BatchSpanProcessor(JsonlSpanExporter(jsonl_path)))
    if settings.otel_console_export:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    otel_trace.set_tracer_provider(provider)
    _Init.tracer = provider.get_tracer(TRACER_NAME)

    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)

    if settings.agent_runtime == "openai_agents":
        from api.observability.agents_bridge import register_agents_bridge

        register_agents_bridge(
            tracer=_Init.tracer,
            provider_name=infer_provider_name(settings.provider_base_url),
            model_name=settings.model_name,
        )

    _Init.done = True


def _resolve_jsonl_path(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else PROJECT_ROOT / path
