"""The reasoning round-trip model (ADR-0012): the outbound seam echoes
`reasoning_content` back onto the assistant message dict, which is what DeepSeek
thinking mode requires and what stock ChatOpenAI drops. Pure payload check, no
network."""

from __future__ import annotations

import pytest

pytest.importorskip("langchain_openai")

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402
from pydantic import SecretStr  # noqa: E402

from agent.runtime.reasoning_model import ReasoningRoundTripChatOpenAI  # noqa: E402


def _model() -> ReasoningRoundTripChatOpenAI:
    return ReasoningRoundTripChatOpenAI(
        api_key=SecretStr("sk-test"),
        model="deepseek/deepseek-v4",
        base_url="https://openrouter.ai/api/v1",
    )


def test_reasoning_content_injected_on_assistant_message():
    messages = [
        HumanMessage("cheapest 4 vCPU?"),
        AIMessage(
            "let me check",
            additional_kwargs={"reasoning_content": "STEP-BY-STEP-THOUGHTS"},
        ),
        HumanMessage("continue"),
    ]
    payload = _model()._get_request_payload(messages)

    assistant = [m for m in payload["messages"] if m.get("role") == "assistant"]
    assert assistant and assistant[0]["reasoning_content"] == "STEP-BY-STEP-THOUGHTS"
    # Non-assistant messages are untouched.
    for m in payload["messages"]:
        if m.get("role") != "assistant":
            assert "reasoning_content" not in m


def test_no_reasoning_content_means_no_field():
    messages = [HumanMessage("hi"), AIMessage("hello")]
    payload = _model()._get_request_payload(messages)
    for m in payload["messages"]:
        assert "reasoning_content" not in m
