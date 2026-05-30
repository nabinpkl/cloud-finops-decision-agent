"""JsonlSpanExporter writes one OTel-JSON line per span and is replay-safe."""

from __future__ import annotations

import json
from pathlib import Path

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from api.observability import JsonlSpanExporter


def _make_provider(tmp_path: Path) -> tuple[TracerProvider, JsonlSpanExporter, Path]:
    out = tmp_path / "nested" / "traces.jsonl"
    exporter = JsonlSpanExporter(out)
    provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter, out


def test_exporter_writes_one_line_per_span(tmp_path: Path):
    provider, exporter, out = _make_provider(tmp_path)
    tracer = provider.get_tracer("t")

    with tracer.start_as_current_span("first") as s:
        s.set_attribute("k", "v")
    with tracer.start_as_current_span("second"):
        pass

    provider.shutdown()
    exporter.shutdown()

    assert out.exists()
    lines = out.read_text().strip().splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    # Span order is end-time order; "first" closes before "second".
    assert parsed[0]["name"] == "first"
    assert parsed[1]["name"] == "second"
    assert parsed[0]["attributes"]["k"] == "v"


def test_exporter_creates_parent_directory(tmp_path: Path):
    deep = tmp_path / "a" / "b" / "c" / "traces.jsonl"
    exporter = JsonlSpanExporter(deep)
    provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    provider.get_tracer("t").start_span("x").end()
    provider.shutdown()
    exporter.shutdown()
    assert deep.parent.is_dir()
    assert deep.exists()
