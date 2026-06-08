"""Provider usage normalization for budget enforcement.

Reasoning tokens are an output-token detail in the OpenAI/OpenRouter shapes,
not an additional bucket to add on top of provider totals.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class UsageDelta:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    cached_input_tokens: int = 0

    @property
    def budget_tokens(self) -> int:
        return self.total_tokens or (self.input_tokens + self.output_tokens)


def usage_delta(raw: Any) -> UsageDelta:
    """Normalize common provider/framework usage payloads.

    Handles OpenAI Responses, OpenAI-compatible Chat Completions/OpenRouter,
    LangChain `usage_metadata`, and SDK objects with equivalent attributes.
    """
    if raw is None:
        return UsageDelta()

    input_tokens = _first_int(raw, "input_tokens", "prompt_tokens")
    output_tokens = _first_int(raw, "output_tokens", "completion_tokens")
    total_tokens = _first_int(raw, "total_tokens")

    input_details = _first_child(
        raw,
        "input_token_details",
        "input_tokens_details",
        "prompt_tokens_details",
    )
    output_details = _first_child(
        raw,
        "output_token_details",
        "output_tokens_details",
        "completion_tokens_details",
    )

    return UsageDelta(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        reasoning_tokens=_first_int(output_details, "reasoning", "reasoning_tokens"),
        cached_input_tokens=_first_int(
            input_details,
            "cache_read",
            "cached_tokens",
            "cache_creation",
        ),
    )


def _first_child(raw: Any, *names: str) -> Any:
    for name in names:
        value = _get(raw, name)
        if value is not None:
            return value
    return None


def _first_int(raw: Any, *names: str) -> int:
    for name in names:
        value = _get(raw, name)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _get(raw: Any, name: str) -> Any:
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        return raw.get(name)
    return getattr(raw, name, None)
