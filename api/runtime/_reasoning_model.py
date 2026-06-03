"""A `ChatOpenAI` subclass that round-trips `reasoning_content` (ADR-0012).

langchain-openai's base model documents the gap this fills: "(`reasoning_content`,
`reasoning_details`) are not extracted ... Use a provider-specific subclass."
DeepSeek V4 thinking mode (via OpenRouter) requires the assistant's
`reasoning_content` from a tool-calling turn to be echoed back on every
subsequent request, or it returns an empty completion. The stock `ChatOpenAI`
neither captures it from the response nor re-sends it, so the field is silently
dropped and DeepSeek goes blank.

Two overrides, the same field on both ends:

- **Outbound** (`_get_request_payload`): after the base builds the wire
  `messages`, copy `reasoning_content` from each source `AIMessage`'s
  `additional_kwargs` onto the matching assistant message dict. This is the part
  DeepSeek actually requires and the part the base never does.
- **Inbound** (`_convert_chunk_to_generation_chunk`, `_create_chat_result`):
  best-effort capture of `reasoning_content` from the provider's delta / message
  into `additional_kwargs`, so there is something to echo on the next turn. Kept
  defensive: provider chunk shapes vary, and a miss here only degrades to the
  stock behavior, it does not break the turn.

This module is imported only when `settings.langchain_reasoning_roundtrip` is
True. For a deployment that drops OpenRouter, `langchain_deepseek.ChatDeepSeek`
(direct API) already round-trips and is the simpler choice.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI

_REASONING_KEYS = ("reasoning_content", "reasoning")


def _delta_reasoning(chunk: dict) -> str | None:
    try:
        delta = chunk["choices"][0].get("delta") or {}
    except (KeyError, IndexError, AttributeError, TypeError):
        return None
    for key in _REASONING_KEYS:
        value = delta.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _message_reasoning(response: Any) -> str | None:
    try:
        if isinstance(response, dict):
            message = response["choices"][0]["message"]
            get = message.get
        else:  # openai BaseModel
            message = response.choices[0].message
            get = lambda k: getattr(message, k, None)  # noqa: E731
    except (KeyError, IndexError, AttributeError, TypeError):
        return None
    for key in _REASONING_KEYS:
        value = get(key)
        if isinstance(value, str) and value:
            return value
    return None


class ReasoningRoundTripChatOpenAI(ChatOpenAI):
    """`ChatOpenAI` that captures and re-sends `reasoning_content`."""

    def _get_request_payload(
        self, input_: Any, *, stop: list[str] | None = None, **kwargs: Any
    ) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        messages = input_ if isinstance(input_, list) else None
        if not messages:
            return payload
        # The base converts messages to wire dicts in order, so walking the
        # assistant dicts and the source AIMessages in parallel keeps them
        # aligned without tracking positions through the base's conversion.
        ai_messages = [m for m in messages if isinstance(m, AIMessage)]
        i = 0
        for wire in payload.get("messages", []):
            if wire.get("role") != "assistant":
                continue
            if i < len(ai_messages):
                reasoning = ai_messages[i].additional_kwargs.get("reasoning_content")
                if isinstance(reasoning, str) and reasoning:
                    wire["reasoning_content"] = reasoning
                i += 1
        return payload

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> ChatGenerationChunk | None:
        generation = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if generation is None:
            return None
        piece = _delta_reasoning(chunk)
        if piece:
            # Per-chunk pieces concatenate when AIMessageChunks merge, so the
            # aggregated message carries the full reasoning string.
            generation.message.additional_kwargs["reasoning_content"] = piece
        return generation

    def _create_chat_result(
        self, response: Any, generation_info: dict | None = None
    ) -> ChatResult:
        result = super()._create_chat_result(response, generation_info)
        reasoning = _message_reasoning(response)
        if reasoning and result.generations:
            result.generations[0].message.additional_kwargs["reasoning_content"] = (
                reasoning
            )
        return result
