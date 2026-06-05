"""Cost computation for known/unknown models and zero usage."""

from __future__ import annotations

import api.observability as obs


def test_known_model_computes_cost():
    cost, known = obs.compute_cost_usd(
        model="gpt-4.1",
        usage={"input_tokens": 1_000_000, "output_tokens": 250_000},
    )
    # gpt-4.1 priced 2.00/1M in, 8.00/1M out -> 2.00 + 0.25*8.00 = 4.00
    assert known is True
    assert cost == 4.00


def test_unknown_model_returns_zero_and_flag():
    cost, known = obs.compute_cost_usd(
        model="some/unknown-model-x",
        usage={"input_tokens": 1_000_000, "output_tokens": 1_000_000},
    )
    assert cost == 0.0
    assert known is False


def test_known_model_with_no_usage_returns_zero():
    cost, known = obs.compute_cost_usd(model="gpt-4.1", usage=None)
    assert cost == 0.0
    assert known is True  # the model is known; nothing happened on it


def test_alternate_token_field_names_are_accepted():
    # Some SDK paths emit prompt_tokens/completion_tokens instead.
    cost, known = obs.compute_cost_usd(
        model="gpt-4.1-mini",
        usage={"prompt_tokens": 1_000_000, "completion_tokens": 0},
    )
    # gpt-4.1-mini priced 0.40/1M in
    assert known is True
    assert cost == 0.40
