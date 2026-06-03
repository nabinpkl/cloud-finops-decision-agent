"""OpenAI Agents SDK -> OTel tracing bridge (ADR-0010, ADR-0012).

Isolated from `api/observability.py` so that importing observability (which
`api/main.py` loads on every startup) does not import the `agents` package.
This module is imported lazily by `init_observability` only when
`AGENT_RUNTIME=openai_agents`, which is what lets `openai-agents` be an optional
dependency: the default (langchain) runtime never loads it.

It translates the SDK's trace/span lifecycle into OTel spans so the agent loop,
model calls, and tool calls land in the same trace tree as the FastAPI HTTP
span. The neutral `agent.turn` span set in `api/transport.py` is independent of
this bridge and carries cross-runtime attributes regardless.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from agents.tracing import Span as AgentsSpan
from agents.tracing import Trace as AgentsTrace
from agents.tracing import add_trace_processor
from agents.tracing.processor_interface import TracingProcessor
from agents.tracing.span_data import (
    AgentSpanData,
    FunctionSpanData,
    GenerationSpanData,
    ResponseSpanData,
    SpanData,
    TaskSpanData,
    TurnSpanData,
)
from opentelemetry import trace as otel_trace
from opentelemetry.semconv._incubating.attributes import gen_ai_attributes as g
from opentelemetry.trace import Span as OtelSpan
from opentelemetry.trace import Status, StatusCode

from api.observability import compute_cost_usd


def _set_genai_attrs(
    otel_span: OtelSpan,
    span_data: SpanData,
    provider_name: str,
    model_name: str,
) -> None:
    """Translate the SDK's span_data fields into gen_ai.* + finops.* attrs.
    Centralized so the schema lives in one place."""
    otel_span.set_attribute(g.GEN_AI_PROVIDER_NAME, provider_name)
    if model_name:
        otel_span.set_attribute(g.GEN_AI_REQUEST_MODEL, model_name)

    if isinstance(span_data, AgentSpanData):
        otel_span.set_attribute(g.GEN_AI_AGENT_NAME, span_data.name)
        otel_span.set_attribute(g.GEN_AI_OPERATION_NAME, "invoke_agent")
        if span_data.tools:
            otel_span.set_attribute("finops.agent.tools", list(span_data.tools))

    elif isinstance(span_data, (TurnSpanData, TaskSpanData, GenerationSpanData)):
        otel_span.set_attribute(g.GEN_AI_OPERATION_NAME, "chat")
        usage = getattr(span_data, "usage", None)
        if usage:
            in_tokens  = int(usage.get("input_tokens")  or usage.get("prompt_tokens")     or 0)
            out_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
            otel_span.set_attribute(g.GEN_AI_USAGE_INPUT_TOKENS,  in_tokens)
            otel_span.set_attribute(g.GEN_AI_USAGE_OUTPUT_TOKENS, out_tokens)
            otel_span.set_attribute("gen_ai.usage.total_tokens", in_tokens + out_tokens)
            generation_model = getattr(span_data, "model", None) or model_name
            cost, known = compute_cost_usd(generation_model, usage)
            otel_span.set_attribute("finops.cost_usd",       cost)
            otel_span.set_attribute("finops.cost.estimate",  True)
            if not known:
                otel_span.set_attribute("finops.cost.unknown_model", True)

    elif isinstance(span_data, FunctionSpanData):
        otel_span.set_attribute(g.GEN_AI_TOOL_NAME, span_data.name)
        otel_span.set_attribute(g.GEN_AI_OPERATION_NAME, "execute_tool")
        if span_data.input is not None:
            otel_span.set_attribute("finops.tool.args_size_bytes", len(str(span_data.input)))
        if span_data.output is not None:
            otel_span.set_attribute("finops.tool.result_size_bytes", len(str(span_data.output)))

    elif isinstance(span_data, ResponseSpanData):
        usage = getattr(span_data, "usage", None)
        if usage:
            in_tokens  = int(usage.get("input_tokens")  or 0)
            out_tokens = int(usage.get("output_tokens") or 0)
            otel_span.set_attribute(g.GEN_AI_USAGE_INPUT_TOKENS,  in_tokens)
            otel_span.set_attribute(g.GEN_AI_USAGE_OUTPUT_TOKENS, out_tokens)
            cost, known = compute_cost_usd(model_name, usage)
            otel_span.set_attribute("finops.cost_usd",       cost)
            otel_span.set_attribute("finops.cost.estimate",  True)
            if not known:
                otel_span.set_attribute("finops.cost.unknown_model", True)


def _otel_span_name(span_data: SpanData) -> str:
    t = span_data.type
    return {
        "agent":      "agent.invoke",
        "task":       "agent.run",
        "turn":       "agent.turn",
        "generation": "model.chat",
        "response":   "model.response",
        "function":   "tool.execute",
    }.get(t, f"agents.{t}")


class AgentsSdkOtelProcessor(TracingProcessor):
    """Translate the agents-SDK trace/span lifecycle into OTel spans.

    The SDK calls `on_trace_start` / `on_span_start` / `on_span_end` /
    `on_trace_end`. Each agents-SDK span_id is mapped to an OTel span. Parents
    are resolved through the SDK's `parent_id` chain; the trace root is parented
    to whatever OTel span is current when `on_trace_start` fires (typically the
    manual `agent.turn` span set inside `api/transport.py.run_callback`).
    """

    def __init__(self, tracer: otel_trace.Tracer, provider_name: str, model_name: str):
        self._tracer        = tracer
        self._provider_name = provider_name
        self._model_name    = model_name
        self._lock          = threading.Lock()
        self._roots:        dict[str, OtelSpan] = {}
        self._spans:        dict[str, OtelSpan] = {}
        # Track context tokens so we can detach() spans we attached.
        self._tokens:       dict[str, object] = {}

    # ----- trace lifecycle -----

    def on_trace_start(self, trace: AgentsTrace) -> None:
        with self._lock:
            otel_span = self._tracer.start_span(trace.name or "agent.run")
            otel_span.set_attribute(g.GEN_AI_PROVIDER_NAME, self._provider_name)
            if self._model_name:
                otel_span.set_attribute(g.GEN_AI_REQUEST_MODEL, self._model_name)
            self._roots[trace.trace_id] = otel_span

    def on_trace_end(self, trace: AgentsTrace) -> None:
        with self._lock:
            root = self._roots.pop(trace.trace_id, None)
            if root is not None:
                root.end()

    # ----- span lifecycle -----

    def on_span_start(self, span: AgentsSpan[Any]) -> None:
        with self._lock:
            parent_otel = self._spans.get(span.parent_id or "") if span.parent_id else None
            if parent_otel is None:
                parent_otel = self._roots.get(span.trace_id)
            ctx = otel_trace.set_span_in_context(parent_otel) if parent_otel is not None else None
            otel_span = self._tracer.start_span(
                _otel_span_name(span.span_data),
                context=ctx,
            )
            self._spans[span.span_id] = otel_span

    def on_span_end(self, span: AgentsSpan[Any]) -> None:
        with self._lock:
            otel_span = self._spans.pop(span.span_id, None)
        if otel_span is None:
            return
        try:
            _set_genai_attrs(otel_span, span.span_data, self._provider_name, self._model_name)
            if span.error is not None:
                otel_span.set_status(Status(StatusCode.ERROR, span.error.get("message") or ""))
                err_data = span.error.get("data")
                if err_data:
                    otel_span.set_attribute("finops.error.data", json.dumps(err_data, default=str))
        finally:
            otel_span.end()

    # ----- processor lifecycle -----

    def shutdown(self) -> None:
        with self._lock:
            for s in list(self._spans.values()):
                s.end()
            for r in list(self._roots.values()):
                r.end()
            self._spans.clear()
            self._roots.clear()

    def force_flush(self) -> None:
        # OTel batch processor flushes; we have no buffer of our own.
        return None


def register_agents_bridge(
    tracer: otel_trace.Tracer, provider_name: str, model_name: str
) -> None:
    """Register the bridge as an agents-SDK trace processor. Called from
    `init_observability` only under the openai_agents runtime."""
    add_trace_processor(
        AgentsSdkOtelProcessor(
            tracer=tracer, provider_name=provider_name, model_name=model_name
        )
    )
