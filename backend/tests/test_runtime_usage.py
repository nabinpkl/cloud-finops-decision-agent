from __future__ import annotations

from types import SimpleNamespace

from agent.runtime.types import RunUsage
from agent.runtime.usage import usage_delta


def test_openai_responses_reasoning_uses_provider_total_not_double_count():
    delta = usage_delta(
        {
            "input_tokens": 81,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 1035,
            "output_tokens_details": {"reasoning_tokens": 832},
            "total_tokens": 1116,
        }
    )

    assert delta.input_tokens == 81
    assert delta.output_tokens == 1035
    assert delta.reasoning_tokens == 832
    assert delta.budget_tokens == 1116


def test_openrouter_chat_usage_aliases_are_normalized():
    delta = usage_delta(
        {
            "prompt_tokens": 20,
            "completion_tokens": 30,
            "total_tokens": 50,
            "prompt_tokens_details": {"cached_tokens": 7},
            "completion_tokens_details": {"reasoning_tokens": 11},
        }
    )

    assert delta.input_tokens == 20
    assert delta.output_tokens == 30
    assert delta.cached_input_tokens == 7
    assert delta.reasoning_tokens == 11
    assert delta.budget_tokens == 50


def test_langchain_usage_metadata_details_are_normalized():
    delta = usage_delta(
        {
            "input_tokens": 8,
            "output_tokens": 304,
            "total_tokens": 312,
            "input_token_details": {"cache_read": 4},
            "output_token_details": {"reasoning": 256},
        }
    )

    assert delta.cached_input_tokens == 4
    assert delta.reasoning_tokens == 256
    assert delta.budget_tokens == 312


def test_object_usage_and_missing_total_falls_back_to_input_plus_output():
    delta = usage_delta(
        SimpleNamespace(
            input_tokens=5,
            output_tokens=9,
            output_tokens_details=SimpleNamespace(reasoning_tokens=3),
        )
    )

    assert delta.reasoning_tokens == 3
    assert delta.budget_tokens == 14


def test_run_usage_add_call_accumulates_total_and_details():
    usage = RunUsage()
    usage.add_call(
        input_tokens=81,
        output_tokens=1035,
        total_tokens=1116,
        reasoning_tokens=832,
        cached_input_tokens=10,
    )
    usage.add_call(input_tokens=1, output_tokens=2)

    assert usage.input_tokens == 82
    assert usage.output_tokens == 1037
    assert usage.reasoning_tokens == 832
    assert usage.cached_input_tokens == 10
    assert usage.total == 1119
    assert usage.llm_calls == 2
