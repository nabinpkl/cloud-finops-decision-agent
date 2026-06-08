"""Agent-loop budget enforcement for the OpenAI Agents SDK (ADR-0011 seam [5],
ADR-0012).

`BudgetHooks` subclasses the Agents SDK's `RunHooksBase` and watches every
LLM call inside a single `Runner.run_streamed(...)`. After each call it
adds the response's token usage to a per-run counter; once the cumulative
total crosses `settings.turn_token_cap` the hook raises the neutral
`TurnTokenCapExceeded` (defined in `agent/runtime/types.py`), which propagates up
through the SDK and is caught by `api/assistant_transport/turn.py` (where it is
recorded on the OTel `agent.turn` span and surfaced to the visible thread).
This file is part of the OpenAI-agents adapter; the LangChain adapter enforces
the same neutral cap via its own middleware.

Cumulative counters are kept on `self` because the SDK does not expose a
"last-delta" hook; reading `context.usage` each time is equivalent for
single-agent runs but more fragile across SDK upgrades.

This module does **not** persist usage. Persistence runs in
`api/assistant_transport/turn.py`'s `finally` block via
`budget_store.record_usage` so a partially-streamed turn still pays for what it
consumed.

`TurnTokenCapExceeded` is re-exported here for backward compatibility; its
canonical home is `agent.runtime.types`.
"""

from __future__ import annotations

from typing import Any

from agents.items import ModelResponse
from agents.lifecycle import RunHooksBase
from agents.run_context import RunContextWrapper

from app_config import settings
from agent.runtime.types import RunUsage, TurnTokenCapExceeded
from agent.runtime.usage import usage_delta

__all__ = ["BudgetHooks", "TurnTokenCapExceeded"]


class BudgetHooks(RunHooksBase[Any, Any]):
    """Accumulates per-turn token totals across every LLM call in a single
    `Runner.run_streamed(...)`. Read by the assistant turn runner after the run for
    record_usage; reads from `cap` are not done here because the cap is
    settings-derived.
    """

    def __init__(self) -> None:
        super().__init__()
        self.usage = RunUsage()

    @property
    def turn_total_tokens(self) -> int:
        return self.usage.total

    @property
    def turn_input_tokens(self) -> int:
        return self.usage.input_tokens

    @property
    def turn_output_tokens(self) -> int:
        return self.usage.output_tokens

    @property
    def llm_calls(self) -> int:
        return self.usage.llm_calls

    async def on_llm_end(
        self,
        context: RunContextWrapper[Any],
        agent: Any,
        response: ModelResponse,
    ) -> None:
        delta = usage_delta(response.usage)
        self.usage.add_call(
            input_tokens=delta.input_tokens,
            output_tokens=delta.output_tokens,
            total_tokens=delta.total_tokens,
            reasoning_tokens=delta.reasoning_tokens,
            cached_input_tokens=delta.cached_input_tokens,
        )
        if self.turn_total_tokens >= settings.turn_token_cap:
            raise TurnTokenCapExceeded(
                input_tokens=self.usage.input_tokens,
                output_tokens=self.usage.output_tokens,
                total_tokens=self.usage.total,
                reasoning_tokens=self.usage.reasoning_tokens,
                cap=settings.turn_token_cap,
            )
