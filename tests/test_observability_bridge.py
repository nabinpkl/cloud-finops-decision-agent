"""End-to-end bridge test: drive the agents-SDK tracing context managers and
assert OTel spans are produced with the expected gen_ai.*/finops.* attributes
and parent linkage."""

from __future__ import annotations

from typing import Any

import agents.tracing as agents_tracing
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from api.observability import AgentsSdkOtelProcessor


def _attrs(span: ReadableSpan) -> dict[str, Any]:
    assert span.attributes is not None
    return dict(span.attributes)


def _build_otel() -> tuple[TracerProvider, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def test_bridge_emits_otel_spans_with_genai_attrs():
    provider, exporter = _build_otel()
    tracer = provider.get_tracer("test")
    bridge = AgentsSdkOtelProcessor(
        tracer=tracer, provider_name="openai", model_name="gpt-4.1"
    )
    agents_tracing.set_trace_processors([bridge])

    with agents_tracing.trace("finops_q"):
        with agents_tracing.agent_span(name="finops", tools=["compare"]):
            with agents_tracing.turn_span(turn=1, agent_name="finops"):
                with agents_tracing.generation_span(
                    model="gpt-4.1",
                    usage={"input_tokens": 1_000_000, "output_tokens": 250_000},
                ):
                    pass
                with agents_tracing.function_span(
                    name="compare",
                    input='{"vcpu":4,"ram_gb":8}',
                    output='{"results":[{"provider":"aws"}]}',
                ):
                    pass

    provider.shutdown()

    spans = {s.name: s for s in exporter.get_finished_spans()}

    # Hierarchy translated correctly: trace root, agent.invoke, agent.turn,
    # model.chat (generation), tool.execute (function).
    assert "finops_q"      in spans
    assert "agent.invoke"  in spans
    assert "agent.turn"    in spans
    assert "model.chat"    in spans
    assert "tool.execute"  in spans

    chat = _attrs(spans["model.chat"])
    assert chat["gen_ai.provider.name"]      == "openai"
    assert chat["gen_ai.request.model"]      == "gpt-4.1"
    assert chat["gen_ai.usage.input_tokens"]  == 1_000_000
    assert chat["gen_ai.usage.output_tokens"] == 250_000
    # gpt-4.1: 2.00/1M in + 8.00/1M out -> 2.00 + 0.25*8.00 = 4.00
    assert chat["finops.cost_usd"] == 4.00
    assert chat["finops.cost.estimate"] is True

    tool = _attrs(spans["tool.execute"])
    assert tool["gen_ai.tool.name"]               == "compare"
    assert tool["gen_ai.operation.name"]          == "execute_tool"
    assert int(tool["finops.tool.args_size_bytes"])   > 0
    assert int(tool["finops.tool.result_size_bytes"]) > 0


def test_bridge_flags_unknown_model_cost():
    provider, exporter = _build_otel()
    tracer = provider.get_tracer("test")
    bridge = AgentsSdkOtelProcessor(
        tracer=tracer, provider_name="local", model_name="some/unreleased-model"
    )
    agents_tracing.set_trace_processors([bridge])

    with agents_tracing.trace("q"):
        with agents_tracing.generation_span(
            model="some/unreleased-model",
            usage={"input_tokens": 100, "output_tokens": 100},
        ):
            pass

    provider.shutdown()

    chat = _attrs(next(s for s in exporter.get_finished_spans() if s.name == "model.chat"))
    assert chat["finops.cost_usd"] == 0.0
    assert chat["finops.cost.unknown_model"] is True
