"""AG-UI transport spine (ADR-0016).

The backend is an AG-UI server: ``POST /assistant`` accepts an AG-UI
``RunAgentInput`` body (the shape ``@ag-ui/client``'s ``HttpAgent`` sends — the
user's text in ``messages``, no ``commands`` field) and streams AG-UI protocol
events (``RUN_STARTED`` / text+tool events / ``STATE_SNAPSHOT`` / ``RUN_FINISHED``)
over SSE, carrying the backend-authoritative view-state. These tests post the
real RunAgentInput shape the shipped frontend sends and verify the wire shape
and the state channel; the hardening-surface contracts are covered in
``test_assistant_transport_security.py`` and the budget suites, which run
against the same migrated endpoint.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.budget.store as budget_store
from agent.guardrails.models import GuardDecision
from agent.guardrails.receipts import result_from_decision
from agent.runtime import Emitter, RunUsage, Turn
from app_config import settings


def _run_agent_input(text: str, **extra) -> dict:
    """Build the AG-UI ``RunAgentInput`` body the shipped frontend POSTs.

    Matches ``@ag-ui/client`` ``HttpAgent``: ``{threadId, runId, state,
    messages, tools, context, forwardedProps}`` with the user's text inside a
    ``messages[]`` entry (``{id, role, content}``) and NO ``commands`` field.
    """
    body = {
        "threadId": f"thread_{uuid.uuid4().hex}",
        "runId": f"run_{uuid.uuid4().hex}",
        "state": {},
        "messages": [
            {"id": f"msg_{uuid.uuid4().hex}", "role": "user", "content": text}
        ],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }
    body.update(extra)
    return body


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


def test_run_agent_input_round_trip_and_state_snapshot(
    monkeypatch: pytest.MonkeyPatch,
):
    import api.assistant_transport.turn as turn_module
    import api.main as apimain

    monkeypatch.setattr(turn_module, "get_runtime", lambda: TextRuntime("hello"))
    client = TestClient(apimain.app)
    # The exact body @ag-ui/client posts — no `commands`, text in `messages`.
    response = client.post("/assistant", json=_run_agent_input("hi"))

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
    # The user message from RunAgentInput.messages drives a real turn: the
    # backend ran the agent and rendered an assistant message into the snapshot.
    assistant = [m for m in snapshot["messages"] if m["role"] == "assistant"]
    assert assistant and any(
        p.get("type") == "text" and "pricing citation policy" in (p.get("text") or "")
        for p in assistant[-1]["parts"]
    )
    text_content = "".join(
        e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT"
    )
    assert "pricing citation policy" in text_content


def test_no_user_message_runs_no_turn(monkeypatch: pytest.MonkeyPatch):
    import api.assistant_transport.turn as turn_module
    import api.main as apimain

    ran = {"called": False}

    class GuardRuntime:
        async def run(self, turns, emit, usage):
            ran["called"] = True
            emit.text_delta("ok")

    monkeypatch.setattr(turn_module, "get_runtime", lambda: GuardRuntime())
    client = TestClient(apimain.app)
    # A trailing assistant message is not a turn trigger.
    body = _run_agent_input("hi")
    body["messages"].append(
        {"id": f"msg_{uuid.uuid4().hex}", "role": "assistant", "content": "prior"}
    )
    response = client.post("/assistant", json=body)

    assert response.status_code == 200
    assert ran["called"] is False
    types = [e["type"] for e in _sse_events(response.text)]
    assert "RUN_STARTED" in types
    assert "RUN_FINISHED" in types


def test_client_supplied_view_state_is_discarded(monkeypatch: pytest.MonkeyPatch):
    import api.assistant_transport.turn as turn_module
    import api.main as apimain

    monkeypatch.setattr(turn_module, "get_runtime", lambda: TextRuntime("ok"))
    client = TestClient(apimain.app)
    response = client.post(
        "/assistant",
        json=_run_agent_input(
            "hi",
            state={
                "messages": [],
                "view": {"columns": ["INJECTED"]},
                "selection": {"rows": [99], "highlight": "x"},
            },
        ),
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
    response = client.post("/assistant", json=_run_agent_input("hi"))

    assert response.status_code == 200
    types = [e["type"] for e in _sse_events(response.text)]
    assert "TOOL_CALL_START" in types
    assert "TOOL_CALL_ARGS" in types
    assert "TOOL_CALL_END" in types
    assert "TOOL_CALL_RESULT" in types


def test_set_view_tool_result_mutates_view_state(monkeypatch: pytest.MonkeyPatch):
    import api.assistant_transport.turn as turn_module
    import api.main as apimain
    from agent.tools.view import run_set_view

    class ViewRuntime:
        async def run(self, turns, emit, usage):
            # A validated compare result must exist for the view rows to bind.
            emit.tool_call("call-c", "compare", "{}", {})
            emit.tool_result(
                "call-c",
                {
                    "results": [
                        {"provider": "aws", "instance_type": "m5.xlarge"},
                    ]
                },
            )
            emit.tool_call("call-v", "set_view", "{}", {})
            emit.tool_result(
                "call-v",
                run_set_view(
                    columns=[{"column_id": "provider"}],
                    source_result_indices=[0],
                ),
            )

    monkeypatch.setattr(turn_module, "get_runtime", lambda: ViewRuntime())
    client = TestClient(apimain.app)
    response = client.post("/assistant", json=_run_agent_input("show table"))

    assert response.status_code == 200
    snapshot = next(
        e for e in _sse_events(response.text) if e["type"] == "STATE_SNAPSHOT"
    )["snapshot"]
    # The co-driver tool result lands in the backend-authoritative view-state
    # only after registry + row-binding validation passes.
    assert snapshot["view"]["columns"][0]["column_id"] == "provider"


def test_legacy_commands_shape_still_accepted(monkeypatch: pytest.MonkeyPatch):
    """The prior assistant-ui transport contract (state + commands) keeps working."""
    import api.assistant_transport.turn as turn_module
    import api.main as apimain

    monkeypatch.setattr(turn_module, "get_runtime", lambda: TextRuntime("hello"))
    client = TestClient(apimain.app)
    response = client.post(
        "/assistant",
        json={
            "commands": [
                {
                    "type": "add-message",
                    "message": {
                        "role": "user",
                        "parts": [{"type": "text", "text": "hi"}],
                    },
                }
            ]
        },
    )

    assert response.status_code == 200
    types = [e["type"] for e in _sse_events(response.text)]
    assert types[0] == "RUN_STARTED"
    assert types[-1] == "RUN_FINISHED"
    assert "STATE_SNAPSHOT" in types
