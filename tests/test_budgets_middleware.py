"""BudgetMiddleware: global cap -> 503, client rate -> 429, /assistant
scoping, off-switch."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import budgets
from api.config import settings
from api.middleware import BudgetMiddleware


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(BudgetMiddleware)

    @app.post("/assistant")
    def assistant() -> dict:
        return {"ok": True}

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    return app


@pytest.fixture
def budgets_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "budget_enabled", True)
    monkeypatch.setattr(settings, "budget_db_path", str(tmp_path / "b.db"))
    monkeypatch.setattr(settings, "budget_ip_hash_salt_secret", "test-salt-XX")
    monkeypatch.setattr(settings, "global_daily_token_cap", 1_000_000)
    monkeypatch.setattr(settings, "client_rate_requests_per_minute", 3)
    monkeypatch.setattr(settings, "client_rate_tokens_per_hour", 1_000_000)
    monkeypatch.setattr(budgets._Init, "done", False)
    monkeypatch.setattr(budgets._Init, "conn", None)
    budgets.init_budgets()
    yield
    if budgets._Init.conn is not None:
        budgets._Init.conn.close()
        budgets._Init.conn = None
        budgets._Init.done = False


def test_assistant_passthrough_when_under_caps(budgets_on):
    client = TestClient(_build_app())
    r = client.post("/assistant", json={})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_health_bypasses_middleware_even_over_global_cap(budgets_on, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "global_daily_token_cap", 0)
    client = TestClient(_build_app())
    r = client.get("/health")
    assert r.status_code == 200


def test_global_cap_returns_503_with_retry_after(budgets_on, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "global_daily_token_cap", 100)
    # Seed today's row past the cap.
    budgets.record_usage("warm", "warm-client", input_tokens=80, output_tokens=80)
    client = TestClient(_build_app())
    r = client.post("/assistant", json={})
    assert r.status_code == 503
    assert "Retry-After" in r.headers
    assert int(r.headers["Retry-After"]) > 0
    assert r.json()["error"] == "global_daily"


def test_client_rate_returns_429_after_burst(budgets_on):
    client = TestClient(_build_app())
    # 3-per-minute cap from the fixture; the 4th should 429.
    for _ in range(3):
        assert client.post("/assistant", json={}).status_code == 200
    r = client.post("/assistant", json={})
    assert r.status_code == 429
    assert r.json()["error"] == "client_request_rate"
    assert int(r.headers["Retry-After"]) > 0


def test_off_switch_passes_everything_through(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "budget_enabled", False)
    client = TestClient(_build_app())
    for _ in range(20):
        assert client.post("/assistant", json={}).status_code == 200
