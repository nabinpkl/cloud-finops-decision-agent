"""The agent-runtime port (ADR-0012): a framework-neutral seam between API
transport adapters and the agent frameworks behind it.

Nothing in this module imports an agent framework. The types here are the only
vocabulary transport speaks: it hands a runtime a list of `Turn`s and an
`Emitter`, the runtime streams output back through the emitter's neutral verbs
(`text_delta`, `tool_call`, `tool_result`) and accumulates token usage into the
caller-owned `RunUsage`. Which framework actually runs is chosen by
`agent.runtime.get_runtime()` off `settings.agent_runtime`.

The split this enforces: the citation contract, budget enforcement, session
identity, and the assistant-ui wire shape are *ours* and live above this port.
The agent loop, model client, tool binding, and stream-event shapes are adapter
concerns and live below it. An adapter may import shared settings and neutral
tool logic; it must never leak a framework type back across this seam.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Turn:
    """One cross-turn conversation message fed into a run. v0 is text-only;
    `role` is one of "user" or "assistant"."""

    role: str
    content: str


@dataclass
class RunUsage:
    """Mutable token accumulator owned by the caller (transport) and written
    by the runtime as the run progresses. Passing it in (rather than returning
    it) means transport can read partial usage in its `finally` even when the
    run aborts mid-stream, so a turn stopped by the cap still pays for what it
    consumed."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    cached_input_tokens: int = 0
    llm_calls: int = 0

    @property
    def total(self) -> int:
        return self.total_tokens or (self.input_tokens + self.output_tokens)

    def add(self, other: "RunUsage") -> None:
        """Merge another usage accumulator into this one."""
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.total_tokens += other.total
        self.reasoning_tokens += other.reasoning_tokens
        self.cached_input_tokens += other.cached_input_tokens
        self.llm_calls += other.llm_calls

    def add_call(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
        reasoning_tokens: int = 0,
        cached_input_tokens: int = 0,
    ) -> None:
        """Add one model call using provider total when available."""
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.total_tokens += total_tokens or (input_tokens + output_tokens)
        self.reasoning_tokens += reasoning_tokens
        self.cached_input_tokens += cached_input_tokens
        if input_tokens or output_tokens or total_tokens:
            self.llm_calls += 1


@runtime_checkable
class Emitter(Protocol):
    """How a runtime streams output back to transport. The implementation
    (`AGUIStateEmitter` in `api/assistant_transport/agui/emitter.py`) maps each
    call onto AG-UI wire events and mirrors the content into the
    backend-authoritative view-state. Runtimes call these verbs; they never
    touch the transport state directly."""

    def text_delta(self, text: str) -> None:
        """Append assistant text. Consecutive deltas coalesce into one text part."""
        ...

    def tool_call(self, call_id: str, name: str, args_text: str, args: dict) -> None:
        """Open a tool-call part. `args_text` is the raw argument JSON string;
        `args` is the parsed object (empty dict if it did not parse)."""
        ...

    def tool_result(self, call_id: str, result: object) -> None:
        """Attach a result to the previously-opened tool-call part and mark it done."""
        ...


class TurnTokenCapExceeded(Exception):
    """Raised by a runtime when the per-turn cumulative token total reaches or
    exceeds `settings.turn_token_cap`. This is a neutral exception, not a
    framework type, so transport catches the same class regardless of which
    runtime raised it: it sets `finops.budget.exhausted=true` and
    `finops.budget.scope="turn"` on the `agent.turn` span and surfaces a visible
    `[turn stopped: ...]` part on the assistant message.
    """

    def __init__(
        self,
        input_tokens: int,
        output_tokens: int,
        cap: int,
        *,
        total_tokens: int = 0,
        reasoning_tokens: int = 0,
    ) -> None:
        total = total_tokens or (input_tokens + output_tokens)
        super().__init__(
            f"turn token cap reached: {total} > {cap} "
            f"(input={input_tokens}, output={output_tokens}, "
            f"reasoning={reasoning_tokens})"
        )
        self.input_tokens     = input_tokens
        self.output_tokens    = output_tokens
        self.total_tokens     = total
        self.reasoning_tokens = reasoning_tokens
        self.cap              = cap


class AgentRuntime(Protocol):
    """The port. One method: run a single turn, streaming through `emit` and
    accumulating into `usage`. Each adapter reads its own caps/limits from
    `settings` (which is ours, framework-neutral); the port does not thread
    them so the signature stays minimal."""

    async def run(self, turns: list[Turn], emit: Emitter, usage: RunUsage) -> None:
        ...
