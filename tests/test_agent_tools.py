"""Mocked tests for the agent's `compare` tool logic (api/tools_core.py).

The neutral tool body lives in `api/tools_core.py` (ADR-0012); the OpenAI-agents
binding in `api/tools.py` and the future DeepAgents binding both wrap it. The
agent shares its citation translation with the HTTP surface via `api/wire.py`:
every tool result the model sees has its `store_path` stripped and replaced with
a logical `snapshot` ref. These tests pin that contract using the same canned
compare payloads as test_integration_api.py.
"""

from __future__ import annotations

import api.tools_core as tools_core
from tests.test_integration_api import CANNED_COMPARE


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
