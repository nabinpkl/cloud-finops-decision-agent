"""OpenAI Agents SDK to OpenTelemetry tracing bridge."""

from __future__ import annotations

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

from api.observability.cost import compute_cost_usd
from api.observability.redaction import set_sdk_error


def _set_genai_attrs(
    otel_span: OtelSpan,
    span_data: SpanData,
    provider_name: str,
    model_name: str,
) -> None:
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
            in_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
            out_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
            otel_span.set_attribute(g.GEN_AI_USAGE_INPUT_TOKENS, in_tokens)
            otel_span.set_attribute(g.GEN_AI_USAGE_OUTPUT_TOKENS, out_tokens)
            otel_span.set_attribute("gen_ai.usage.total_tokens", in_tokens + out_tokens)
            generation_model = getattr(span_data, "model", None) or model_name
            cost, known = compute_cost_usd(generation_model, usage)
            otel_span.set_attribute("finops.cost_usd", cost)
            otel_span.set_attribute("finops.cost.estimate", True)
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
            in_tokens = int(usage.get("input_tokens") or 0)
            out_tokens = int(usage.get("output_tokens") or 0)
            otel_span.set_attribute(g.GEN_AI_USAGE_INPUT_TOKENS, in_tokens)
            otel_span.set_attribute(g.GEN_AI_USAGE_OUTPUT_TOKENS, out_tokens)
            cost, known = compute_cost_usd(model_name, usage)
            otel_span.set_attribute("finops.cost_usd", cost)
            otel_span.set_attribute("finops.cost.estimate", True)
            if not known:
                otel_span.set_attribute("finops.cost.unknown_model", True)


def _otel_span_name(span_data: SpanData) -> str:
    return {
        "agent": "agent.invoke",
        "task": "agent.run",
        "turn": "agent.turn",
        "generation": "model.chat",
        "response": "model.response",
        "function": "tool.execute",
    }.get(span_data.type, f"agents.{span_data.type}")


class AgentsSdkOtelProcessor(TracingProcessor):
    """Translate the agents-SDK trace/span lifecycle into OTel spans."""

    def __init__(self, tracer: otel_trace.Tracer, provider_name: str, model_name: str):
        self._tracer = tracer
        self._provider_name = provider_name
        self._model_name = model_name
        self._lock = threading.Lock()
        self._roots: dict[str, OtelSpan] = {}
        self._spans: dict[str, OtelSpan] = {}

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

    def on_span_start(self, span: AgentsSpan[Any]) -> None:
        with self._lock:
            parent_otel = self._spans.get(span.parent_id or "") if span.parent_id else None
            if parent_otel is None:
                parent_otel = self._roots.get(span.trace_id)
            ctx = otel_trace.set_span_in_context(parent_otel) if parent_otel is not None else None
            self._spans[span.span_id] = self._tracer.start_span(
                _otel_span_name(span.span_data),
                context=ctx,
            )

    def on_span_end(self, span: AgentsSpan[Any]) -> None:
        with self._lock:
            otel_span = self._spans.pop(span.span_id, None)
        if otel_span is None:
            return
        try:
            _set_genai_attrs(otel_span, span.span_data, self._provider_name, self._model_name)
            if span.error is not None:
                set_sdk_error(
                    otel_span,
                    {
                        "message": span.error.get("message"),
                        "data": span.error.get("data"),
                    },
                )
        finally:
            otel_span.end()

    def shutdown(self) -> None:
        with self._lock:
            for span in list(self._spans.values()):
                span.end()
            for root in list(self._roots.values()):
                root.end()
            self._spans.clear()
            self._roots.clear()

    def force_flush(self) -> None:
        return None


def register_agents_bridge(
    tracer: otel_trace.Tracer, provider_name: str, model_name: str
) -> None:
    add_trace_processor(
        AgentsSdkOtelProcessor(
            tracer=tracer,
            provider_name=provider_name,
            model_name=model_name,
        )
    )
