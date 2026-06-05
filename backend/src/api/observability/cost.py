"""Best-effort model cost view."""

from __future__ import annotations

from typing import Any

# Per-1M-token USD prices: (input, output). Best-effort, current at v0; update
# in the same change that adds a new model to the project. Keys match the
# `settings.model_name` string as the user writes it in .env.
PRICE_TABLE: dict[str, tuple[float, float]] = {
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "anthropic/claude-haiku-4-5": (1.00, 5.00),
    "anthropic/claude-sonnet-4-6": (3.00, 15.00),
    "anthropic/claude-opus-4-7": (15.00, 75.00),
}


def compute_cost_usd(model: str, usage: dict[str, Any] | None) -> tuple[float, bool]:
    """Return (cost_usd, known_model)."""
    if not usage:
        return 0.0, model in PRICE_TABLE
    prices = PRICE_TABLE.get(model)
    if prices is None:
        return 0.0, False
    p_in, p_out = prices
    in_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    out_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    return (in_tokens * p_in + out_tokens * p_out) / 1_000_000, True

