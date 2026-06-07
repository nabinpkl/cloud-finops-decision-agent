"""Per-session cumulative token cap: pre-agent check fires when the cookie's
session has already spent over the cap. Confirms the agent runtime is never
obtained (`get_runtime` not called, so no model work) and `sessionLimitReached`
lands in the streamed state."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import api.budget.store as budget_store
from app_config import settings


@pytest.fixture
def caps_low(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "budget_db_path", str(tmp_path / "b.db"))
    monkeypatch.setattr(settings, "budget_ip_hash_salt_secret", "test-salt-XX")
    monkeypatch.setattr(settings, "session_token_cap", 100)
    monkeypatch.setattr(settings, "global_daily_token_cap", 10_000_000)
    monkeypatch.setattr(settings, "client_rate_requests_per_minute", 1000)
    monkeypatch.setattr(settings, "client_rate_tokens_per_hour", 10_000_000)
    monkeypatch.setattr(budget_store._Init, "done", False)
    monkeypatch.setattr(budget_store._Init, "conn", None)
    budget_store.init_budgets()
    yield
    if budget_store._Init.conn is not None:
        budget_store._Init.conn.close()
        budget_store._Init.conn = None
        budget_store._Init.done = False


def _user_msg(text: str) -> dict:
    return {
        "type": "add-message",
        "message": {"role": "user", "parts": [{"type": "text", "text": text}]},
    }


def test_first_request_sets_session_cookie(caps_low):
    import api.main as apimain

    with patch("api.assistant_transport.turn.get_runtime") as get_rt:
        # Runtime isn't obtained because state will lack a user message;
        # but the cookie is set on the response regardless.
        client = TestClient(apimain.app)
        r = client.post(
            "/assistant",
            json={"commands": []},
        )
    assert r.status_code == 200
    assert settings.session_cookie_name in r.cookies
    assert not get_rt.called


def test_session_over_cap_returns_terminal_banner(caps_low):
    import api.main as apimain

    session_id = "exhausted-sess"
    # Push session usage past the 100-token cap.
    budget_store.record_usage(session_id, "client-A", input_tokens=80, output_tokens=80)

    client = TestClient(apimain.app)
    client.cookies.set(settings.session_cookie_name, session_id)
    with patch("api.assistant_transport.turn.get_runtime") as get_rt:
        r = client.post(
            "/assistant",
            json={"commands": [_user_msg("hello")]},
        )

    assert r.status_code == 200
    # The agent runtime must NOT have been obtained or run.
    assert not get_rt.called
    # The streamed body should contain the session-limit text plus the flag
    # write.
    body = r.text
    assert "token limit" in body
    assert "sessionLimitReached" in body
