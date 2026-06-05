"""Agent-loop budget enforcement for the OpenAI Agents SDK (ADR-0011 seam [5],
ADR-0012).

`BudgetHooks` subclasses the Agents SDK's `RunHooksBase` and watches every
LLM call inside a single `Runner.run_streamed(...)`. After each call it
adds the response's token usage to a per-run counter; once the cumulative
total crosses `settings.turn_token_cap` the hook raises the neutral
`TurnTokenCapExceeded` (defined in `api/runtime/types.py`), which propagates up
through the SDK and is caught by `api/transport.py` (where it is recorded on the
OTel `agent.turn` span and surfaced to the visible thread). This file is part of
the OpenAI-agents adapter; the DeepAgents adapter enforces the same neutral cap
via its own middleware.

Cumulative counters are kept on `self` because the SDK does not expose a
"last-delta" hook; reading `context.usage` each time is equivalent for
single-agent runs but more fragile across SDK upgrades.

This module does **not** persist usage. Persistence runs in
`api/transport.py`'s `finally` block via `budget_store.record_usage` so a
partially-streamed turn still pays for what it consumed.

`TurnTokenCapExceeded` is re-exported here for backward compatibility; its
canonical home is `api.runtime.types`.
"""

from __future__ import annotations

from typing import Any

from agents.items import ModelResponse
from agents.lifecycle import RunHooksBase
from agents.run_context import RunContextWrapper

from api.config import settings
from api.runtime.types import TurnTokenCapExceeded

__all__ = ["BudgetHooks", "TurnTokenCapExceeded"]


class BudgetHooks(RunHooksBase[Any, Any]):
    """Accumulates per-turn token totals across every LLM call in a single
    `Runner.run_streamed(...)`. Read by transport.py after the run for
    record_usage; reads from `cap` are not done here because the cap is
    settings-derived.
    """

    def __init__(self) -> None:
        super().__init__()
        self.turn_input_tokens:  int = 0
        self.turn_output_tokens: int = 0
        self.llm_calls:          int = 0

    @property
    def turn_total_tokens(self) -> int:
        return self.turn_input_tokens + self.turn_output_tokens

    async def on_llm_end(
        self,
        context: RunContextWrapper[Any],
        agent: Any,
        response: ModelResponse,
    ) -> None:
        usage = response.usage
        self.turn_input_tokens  += int(usage.input_tokens  or 0)
        self.turn_output_tokens += int(usage.output_tokens or 0)
        self.llm_calls          += 1
        if self.turn_total_tokens >= settings.turn_token_cap:
            raise TurnTokenCapExceeded(
                input_tokens=self.turn_input_tokens,
                output_tokens=self.turn_output_tokens,
                cap=settings.turn_token_cap,
            )
