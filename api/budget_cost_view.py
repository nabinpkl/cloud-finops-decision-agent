"""Operator-facing budget cost estimates."""

from __future__ import annotations

from api.observability import PRICE_TABLE


def tokens_to_usd_view(tokens: int, model_name: str) -> float:
    """Render tokens as approximate USD using the existing price table."""
    prices = PRICE_TABLE.get(model_name)
    if not prices:
        return 0.0
    avg_per_1m = (prices[0] + prices[1]) / 2.0
    return (tokens * avg_per_1m) / 1_000_000
