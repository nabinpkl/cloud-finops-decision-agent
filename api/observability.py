"""OpenTelemetry traces for the agent runtime, written to a local JSONL file
(ADR-0010).

This module owns three concerns kept together so the wiring reads end to end:

1. `JsonlSpanExporter` writes OTel `ReadableSpan` records (one JSON object per
   line) to a configurable file. Uses the SDK's own `ReadableSpan.to_json()`,
   the same encoding the upstream `ConsoleSpanExporter` uses; any OTel-aware
   tool can ingest it. The format is JSON-per-line, not strict OTLP-JSON
   protobuf wire encoding; the seam is identical, swap the exporter when
   sending to a collector.

2. `AgentsSdkOtelProcessor` bridges the OpenAI Agents SDK's tracing
   (`agents/tracing/` `TracingProcessor`) into OTel spans, so the agent loop,
   model calls, and tool calls show up in the same trace tree as the FastAPI
   HTTP span.

3. `compute_cost_usd(model, usage)` is a best-effort cost estimate from a
   hardcoded price table. The result is tagged `finops.cost.estimate=true` so
   dashboards can distinguish it from provider-billed truth; the same
   primitive will back budget enforcement in a follow-up change.

`init_observability(app)` is idempotent; calling it twice (e.g. uvicorn
`--reload`) does not double-instrument.

Spec note: OTel `gen_ai.*` semantic conventions are still incubating. The
constants are imported from `opentelemetry.semconv._incubating.attributes`;
their literal string values are stable enough for v0 (we treat them as wire
contracts, not source ids).
"""

from __future__ import annotations

import os
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlparse

from fastapi import FastAPI
from opentelemetry import trace as otel_trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SpanExporter,
    SpanExportResult,
)

from api.config import settings
from gates._shared import PROJECT_ROOT

TRACER_NAME = "cloud-finops-decision-agent"

# Per-1M-token USD prices: (input, output). Best-effort, current at v0; update
# in the same change that adds a new model to the project. Keys match the
# `settings.model_name` string as the user writes it in .env.
PRICE_TABLE: dict[str, tuple[float, float]] = {
    "gpt-4.1":                          (2.00,  8.00),
    "gpt-4.1-mini":                     (0.40,  1.60),
    "gpt-4.1-nano":                     (0.10,  0.40),
    "gpt-4o":                           (2.50, 10.00),
    "gpt-4o-mini":                      (0.15,  0.60),
    "anthropic/claude-haiku-4-5":       (1.00,  5.00),
    "anthropic/claude-sonnet-4-6":      (3.00, 15.00),
    "anthropic/claude-opus-4-7":       (15.00, 75.00),
}


# ---------- cost ----------


def compute_cost_usd(model: str, usage: dict[str, Any] | None) -> tuple[float, bool]:
    """Return (cost_usd, known_model). If the model isn't in PRICE_TABLE the
    cost is 0.0 and known_model is False so the caller can flag the span."""
    if not usage:
        return 0.0, model in PRICE_TABLE
    prices = PRICE_TABLE.get(model)
    if prices is None:
        return 0.0, False
    p_in, p_out = prices
    in_tokens  = int(usage.get("input_tokens")  or usage.get("prompt_tokens")     or 0)
    out_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    return (in_tokens * p_in + out_tokens * p_out) / 1_000_000, True


# ---------- JSONL exporter ----------


class JsonlSpanExporter(SpanExporter):
    """Append each finished span as one JSON line. Thread-safe via a lock; one
    file handle for the process lifetime, closed in shutdown()."""

    def __init__(self, path: Path) -> None:
        self._path  = path
        self._lock  = threading.Lock()
        self._file: Any | None = None

    def _open(self) -> Any:
        if self._file is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # Line-buffered so a crash doesn't lose the partial buffer.
            self._file = open(self._path, "a", buffering=1, encoding="utf-8")
        return self._file

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with self._lock:
            try:
                handle = self._open()
                for span in spans:
                    # to_json(indent=None) is the SDK's own JSON serialization,
                    # the same one ConsoleSpanExporter uses by default.
                    handle.write(span.to_json(indent=None) + os.linesep)
                return SpanExportResult.SUCCESS
            except OSError:
                return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        with self._lock:
            if self._file is not None:
                try:
                    self._file.flush()
                    self._file.close()
                finally:
                    self._file = None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        with self._lock:
            if self._file is not None:
                self._file.flush()
        return True


# ---------- provider inference ----------


def _infer_provider_name(base_url: str) -> str:
    if not base_url:
        return "unknown"
    host = (urlparse(base_url).hostname or "").lower()
    if "openai.com" in host:
        return "openai"
    if "openrouter" in host:
        return "openrouter"
    if "anthropic" in host:
        return "anthropic"
    if "localhost" in host or "127.0.0.1" in host:
        return "local"
    if "googleapis" in host or "google" in host:
        return "google"
    return host or "unknown"


# ---------- init ----------


class _Init:
    """Module-level latch so calling init_observability twice (reload) is a
    no-op rather than building a second TracerProvider."""

    done: ClassVar[bool] = False
    tracer: ClassVar[otel_trace.Tracer | None] = None


def get_tracer() -> otel_trace.Tracer:
    """Used by `api/transport.py` to create the manual `agent.turn` span. Safe
    to call before init: returns a tracer from the global no-op provider, which
    silently drops spans until init runs."""
    if _Init.tracer is not None:
        return _Init.tracer
    return otel_trace.get_tracer(TRACER_NAME)


def _resolve_jsonl_path(raw: str) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else PROJECT_ROOT / p


def init_observability(app: FastAPI) -> None:
    if _Init.done or not settings.otel_enabled:
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

    # The Agents-SDK -> OTel bridge only has spans to convert when the OpenAI
    # Agents runtime is active (ADR-0012). It lives in a separate module so this
    # one never imports `agents`; under the default langchain runtime
    # (AGENT_RUNTIME=deepagents) it is not imported at all, which is what lets
    # openai-agents be an optional dependency. The neutral `agent.turn` span in
    # transport.py carries cross-runtime token/timing attributes regardless.
    if settings.agent_runtime == "openai_agents":
        from api.observability_agents_bridge import register_agents_bridge

        register_agents_bridge(
            tracer=_Init.tracer,
            provider_name=_infer_provider_name(settings.provider_base_url),
            model_name=settings.model_name,
        )

    _Init.done = True
