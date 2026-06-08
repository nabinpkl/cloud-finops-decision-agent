"""BudgetHooks accumulates per-LLM-call usage and raises
`TurnTokenCapExceeded` exactly when the running total reaches the cap."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest

pytest.importorskip("agents")

from agents.items import ModelResponse  # noqa: E402

from app_config import settings  # noqa: E402
from agent.runtime.openai_agents.hooks import (  # noqa: E402
    BudgetHooks,
    TurnTokenCapExceeded,
)


def _fake_response(
    input_tokens: int,
    output_tokens: int,
    *,
    total_tokens: int | None = None,
    reasoning_tokens: int = 0,
) -> ModelResponse:
    usage = SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens or input_tokens + output_tokens,
        output_tokens_details=SimpleNamespace(reasoning_tokens=reasoning_tokens),
    )
    return cast(ModelResponse, SimpleNamespace(usage=usage))


def test_under_cap_does_not_raise(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "turn_token_cap", 1_000)
    hooks   = BudgetHooks()
    context = MagicMock()
    agent   = MagicMock()
    asyncio.run(hooks.on_llm_end(context, agent, _fake_response(100, 200)))
    asyncio.run(hooks.on_llm_end(context, agent, _fake_response( 50,  50)))
    assert hooks.turn_input_tokens  == 150
    assert hooks.turn_output_tokens == 250
    assert hooks.turn_total_tokens  == 400
    assert hooks.llm_calls          == 2


def test_provider_total_and_reasoning_are_counted(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "turn_token_cap", 2_000)
    hooks = BudgetHooks()
    asyncio.run(
        hooks.on_llm_end(
            MagicMock(),
            MagicMock(),
            _fake_response(81, 1035, total_tokens=1116, reasoning_tokens=832),
        )
    )
    assert hooks.turn_total_tokens == 1116
    assert hooks.usage.reasoning_tokens == 832


def test_first_call_over_cap_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "turn_token_cap", 100)
    hooks = BudgetHooks()
    with pytest.raises(TurnTokenCapExceeded) as exc_info:
        asyncio.run(hooks.on_llm_end(MagicMock(), MagicMock(), _fake_response(60, 60)))
    assert exc_info.value.cap == 100
    assert exc_info.value.input_tokens  == 60
    assert exc_info.value.output_tokens == 60
    assert exc_info.value.total_tokens == 120


def test_cumulative_crossing_raises_on_the_second_call(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "turn_token_cap", 100)
    hooks = BudgetHooks()
    # First call: 80 tokens cumulative. Under 100, no raise.
    asyncio.run(hooks.on_llm_end(MagicMock(), MagicMock(), _fake_response(40, 40)))
    # Second call: pushes to 120. Raise.
    with pytest.raises(TurnTokenCapExceeded):
        asyncio.run(hooks.on_llm_end(MagicMock(), MagicMock(), _fake_response(20, 20)))


def test_exactly_at_cap_raises(monkeypatch: pytest.MonkeyPatch):
    # Cap policy is `>=` so hitting the cap exactly is a stop, not a pass.
    monkeypatch.setattr(settings, "turn_token_cap", 100)
    hooks = BudgetHooks()
    with pytest.raises(TurnTokenCapExceeded):
        asyncio.run(hooks.on_llm_end(MagicMock(), MagicMock(), _fake_response(50, 50)))
