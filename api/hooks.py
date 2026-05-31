"""Agent-loop budget enforcement (ADR-0011, seam [5]).

`BudgetHooks` subclasses the Agents SDK's `RunHooksBase` and watches every
LLM call inside a single `Runner.run_streamed(...)`. After each call it
adds the response's token usage to a per-run counter; once the cumulative
total crosses `settings.turn_token_cap` the hook raises
`TurnTokenCapExceeded`, which propagates up through the SDK and is
caught by the existing `try/except` in `api/transport.py` (where it is
recorded on the OTel `agent.turn` span and surfaced to the visible
thread).

Cumulative counters are kept on `self` because the SDK does not expose a
"last-delta" hook; reading `context.usage` each time is equivalent for
single-agent runs but more fragile across SDK upgrades.

This module does **not** persist usage. Persistence runs in
`api/transport.py`'s `finally` block via `budgets.record_usage` so a
partially-streamed turn still pays for what it consumed.
"""

from __future__ import annotations

from typing import Any

from agents.items import ModelResponse
from agents.lifecycle import RunHooksBase
from agents.run_context import RunContextWrapper

from api.config import settings


class TurnTokenCapExceeded(Exception):
    """Raised by `BudgetHooks.on_llm_end` when the per-turn cumulative
    token total reaches or exceeds `settings.turn_token_cap`.

    Caught in `api/transport.py`; sets `finops.budget.exhausted=true` and
    `finops.budget.scope="turn"` on the `agent.turn` span and converts to
    a visible `[agent error: ...]` part on the assistant message.
    """

    def __init__(self, input_tokens: int, output_tokens: int, cap: int) -> None:
        super().__init__(
            f"turn token cap reached: {input_tokens + output_tokens} > {cap} "
            f"(input={input_tokens}, output={output_tokens})"
        )
        self.input_tokens  = input_tokens
        self.output_tokens = output_tokens
        self.cap           = cap


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
