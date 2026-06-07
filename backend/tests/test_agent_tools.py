"""Mocked tests for the agent's `compare` tool logic.

The neutral tool body lives in `agent.tools.pricing` (ADR-0012); the runtime
adapters wrap it in framework-specific tool bindings. The agent shares its
citation translation with the HTTP surface via `normalize.wire`: every tool
result the model sees has its `store_path` stripped and replaced with a logical
`snapshot` ref. These tests pin that contract using the same canned compare
payloads as test_integration_api.py.
"""

from __future__ import annotations

import agent.tools.pricing as tools_core
from agent.security.untrusted import unwrap_tool_result_json
from test_integration_api import CANNED_COMPARE


def _find_keys(obj, key):
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                found.append(v)
            found.extend(_find_keys(v, key))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_find_keys(item, key))
    return found


def test_compare_tool_strips_store_path_and_adds_snapshot(monkeypatch):
    monkeypatch.setattr(tools_core, "_normalize_compare", lambda **kw: CANNED_COMPARE)

    out = tools_core.run_compare(
        vcpu=4, ram_gb=16, region="eu-central", family="general-purpose"
    )

    assert _find_keys(out, "store_path") == []
    refs = _find_keys(out, "snapshot")
    # 1 atomic AWS citation + 2 composite GCP constituents.
    assert len(refs) == 3
    assert out["results"][0]["citation"]["snapshot"] == {
        "provider":     "aws",
        "snapshot_iso": "2026-05-27T04-23-36Z",
        "filename":     "eu-central-1.json",
    }


def test_compare_for_model_wraps_model_visible_json(monkeypatch):
    monkeypatch.setattr(tools_core, "_normalize_compare", lambda **kw: CANNED_COMPARE)

    model_text, artifact = tools_core.run_compare_for_model(
        vcpu=4,
        ram_gb=16,
        region="eu-central",
        family="general-purpose",
    )

    assert model_text.startswith('<trusted_tool_result tool="compare">')
    assert "<json>" in model_text
    assert "store_path" not in artifact["results"][0]["citation"]
    assert artifact["results"][0]["citation"]["snapshot"]["provider"] == "aws"


def test_openai_agents_compare_returns_wrapped_model_visible_json(monkeypatch):
    import pytest

    pytest.importorskip("agents")
    import agent.runtime.openai_agents.tools as openai_tools

    monkeypatch.setattr(openai_tools, "_run_compare", lambda **kw: CANNED_COMPARE)

    model_text = openai_tools._compare_for_model(
        vcpu=4,
        ram_gb=16,
        region="eu-central",
        family="general-purpose",
    )

    assert model_text.startswith('<trusted_tool_result tool="compare">')
    assert unwrap_tool_result_json(model_text) == CANNED_COMPARE
