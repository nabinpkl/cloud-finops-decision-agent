"""AG-UI transport spine (ADR-0016).

The backend is an AG-UI server: ``POST /assistant`` streams AG-UI protocol
events (``RUN_STARTED`` / text+tool events / ``STATE_SNAPSHOT`` / ``RUN_FINISHED``)
over SSE, carrying the backend-authoritative view-state. These tests verify the
wire shape and the state channel; the hardening-surface contracts are covered in
``test_assistant_transport_security.py`` and the budget suites, which run
against the same migrated endpoint.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.budget.store as budget_store
from agent.guardrails.models import GuardDecision
from agent.guardrails.receipts import result_from_decision
from agent.runtime import Emitter, RunUsage, Turn
from app_config import settings


def _user_msg(text: str) -> dict:
    return {
        "type": "add-message",
        "message": {"role": "user", "parts": [{"type": "text", "text": text}]},
    }


@pytest.fixture(autouse=True)
def transport_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import api.assistant_transport.turn as turn_module

    monkeypatch.setattr(settings, "budget_db_path", str(tmp_path / "b.db"))
    monkeypatch.setattr(settings, "budget_ip_hash_salt_secret", "test-salt-XX")
    monkeypatch.setattr(budget_store._Init, "done", False)
    monkeypatch.setattr(budget_store._Init, "conn", None)
    monkeypatch.setattr(settings, "session_token_cap", 1_000_000)
    monkeypatch.setattr(settings, "global_daily_token_cap", 1_000_000_000)
    monkeypatch.setattr(turn_module, "run_input_guardrail", _guardrail_allow)
    yield
    if budget_store._Init.conn is not None:
        budget_store._Init.conn.close()
        budget_store._Init.conn = None
        budget_store._Init.done = False


async def _guardrail_allow(turns: list[Turn]):
    return result_from_decision(
        GuardDecision(action="allow", reason="safe", confidence=1.0),
        source="test",
        main_model_skipped=False,
    )


def _sse_events(body: str) -> list[dict]:
    events: list[dict] = []
    for block in body.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data:"):
                events.append(json.loads(line[len("data:"):].strip()))
    return events


class TextRuntime:
    def __init__(self, text: str) -> None:
        self._text = text

    async def run(self, turns: list[Turn], emit: Emitter, usage: RunUsage) -> None:
        emit.text_delta(self._text)


def test_run_lifecycle_and_state_snapshot(monkeypatch: pytest.MonkeyPatch):
    import api.assistant_transport.turn as turn_module
    import api.main as apimain

    monkeypatch.setattr(turn_module, "get_runtime", lambda: TextRuntime("hello"))
    client = TestClient(apimain.app)
    response = client.post("/assistant", json={"commands": [_user_msg("hi")]})

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]

    events = _sse_events(response.text)
    types = [e["type"] for e in events]
    assert types[0] == "RUN_STARTED"
    assert types[-1] == "RUN_FINISHED"
    assert "TEXT_MESSAGE_START" in types
    assert "TEXT_MESSAGE_CONTENT" in types
    assert "STATE_SNAPSHOT" in types

    snapshot = next(e for e in events if e["type"] == "STATE_SNAPSHOT")["snapshot"]
    # Backend-authoritative view-state ships in the snapshot.
    assert "messages" in snapshot
    assert "view" in snapshot
    assert "selection" in snapshot
    # Plain prose is not a valid AnswerPlan, so the policy layer substitutes the
    # safe fallback before any text reaches the wire (ADR-0013). The point here
    # is that the rendered text flows text_delta -> AG-UI event -> view-state.
    assistant = [m for m in snapshot["messages"] if m["role"] == "assistant"]
    assert assistant and any(
        p.get("type") == "text" and "pricing citation policy" in (p.get("text") or "")
        for p in assistant[-1]["parts"]
    )
    text_content = "".join(
        e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT"
    )
    assert "pricing citation policy" in text_content


def test_client_supplied_view_state_is_discarded(monkeypatch: pytest.MonkeyPatch):
    import api.assistant_transport.turn as turn_module
    import api.main as apimain

    monkeypatch.setattr(turn_module, "get_runtime", lambda: TextRuntime("ok"))
    client = TestClient(apimain.app)
    response = client.post(
        "/assistant",
        json={
            "state": {
                "messages": [],
                "view": {"columns": ["INJECTED"]},
                "selection": {"rows": [99], "highlight": "x"},
            },
            "commands": [_user_msg("hi")],
        },
    )

    assert response.status_code == 200
    snapshot = next(
        e for e in _sse_events(response.text) if e["type"] == "STATE_SNAPSHOT"
    )["snapshot"]
    # The backend is the only writer of view-state; a client view is reset.
    assert snapshot["view"] is None
    assert snapshot["selection"] == {"rows": [], "highlight": None}


def test_tool_call_events_emitted(monkeypatch: pytest.MonkeyPatch):
    import api.assistant_transport.turn as turn_module
    import api.main as apimain

    class ToolRuntime:
        async def run(self, turns, emit, usage):
            emit.tool_call("call-1", "compare", '{"vcpu":4}', {"vcpu": 4})
            emit.tool_result("call-1", {"results": []})

    monkeypatch.setattr(turn_module, "get_runtime", lambda: ToolRuntime())
    client = TestClient(apimain.app)
    response = client.post("/assistant", json={"commands": [_user_msg("hi")]})

    assert response.status_code == 200
    types = [e["type"] for e in _sse_events(response.text)]
    assert "TOOL_CALL_START" in types
    assert "TOOL_CALL_ARGS" in types
    assert "TOOL_CALL_END" in types
    assert "TOOL_CALL_RESULT" in types
