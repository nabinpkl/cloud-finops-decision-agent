"""Single-turn live smoke against the real model with per-LLM-call context
monitoring. Bypasses HTTP and frontend so the output isolates the agent loop.

Stdout shape (one line per event, parsed by Monitor or `tail -f`):

    [start]   question="..." model="..." provider_host="..."
    [llm-in]  n=1 input_items=2 input_text_chars=1234
    [llm-out] n=1 input_tokens=1200 output_tokens=350 delta_in=+1200
    [tool]    name=compare args_bytes=120 result_bytes=2400
    [llm-in]  n=2 input_items=4 input_text_chars=4567
    [llm-out] n=2 input_tokens=3600 output_tokens=180 delta_in=+2400
    [done]    calls=2 input_total=4800 output_total=530 cost_usd=0.0042 ...
    [answer]  <assistant text on one line>

Run as a module so the package context is set up:

    uv run python -m scripts.agent_smoke

Wrap with `infisical run --env=dev --` (or your usual secrets populator) so
PROVIDER_* and BUDGET_IP_HASH_SALT_SECRET land in env before pydantic-settings
reads them.
"""

from __future__ import annotations

import asyncio
import sys
import time
from typing import Any
from urllib.parse import urlparse

from agents import Runner
from agents.items import ModelResponse, TResponseInputItem
from agents.lifecycle import RunHooksBase
from agents.run_context import RunContextWrapper

from api import budgets
from api.agent import build_agent
from api.config import settings
from api.hooks import TurnTokenCapExceeded
from api.observability import compute_cost_usd

QUESTION = "Cheapest 4 vCPU 8 GB general-purpose VM in eu-central-1?"


def _emit(tag: str, **fields: Any) -> None:
    """Write one structured line to stdout, flush immediately so background
    readers (Monitor, tail -f) see it in real time."""
    parts = [f"[{tag}]"]
    for key, value in fields.items():
        if isinstance(value, str) and (" " in value or "=" in value or '"' in value):
            escaped = value.replace('"', '\\"')
            parts.append(f'{key}="{escaped}"')
        else:
            parts.append(f"{key}={value}")
    line = " ".join(parts)
    print(line, flush=True)


def _input_text_chars(input_items: list[TResponseInputItem]) -> int:
    """Recursively sum lengths of every string leaf across the items. The
    SDK's input shape varies — plain str, dict with `content`, pydantic
    model, function-call output items carrying `output: str`, image-part
    lists. Rather than enumerate shapes, walk the structure and count
    text wherever it appears; numbers and booleans contribute zero so
    structural fields don't inflate the count."""

    def walk(obj: object) -> int:
        if obj is None or isinstance(obj, (int, float, bool)):
            return 0
        if isinstance(obj, str):
            return len(obj)
        if isinstance(obj, dict):
            return sum(walk(v) for v in obj.values())
        if isinstance(obj, (list, tuple)):
            return sum(walk(x) for x in obj)
        dump = getattr(obj, "model_dump", None)
        if callable(dump):
            return walk(dump())
        return 0

    return sum(walk(item) for item in input_items)


class MonitoringHooks(RunHooksBase[Any, Any]):
    """Like BudgetHooks (accumulates per-turn tokens, enforces the cap) but
    also emits one stdout line per llm/tool boundary. Kept separate from
    BudgetHooks so the production hook stays terse."""

    def __init__(self) -> None:
        super().__init__()
        self.calls          = 0
        self.input_tokens   = 0
        self.output_tokens  = 0
        self.prev_input     = 0
        self.tool_calls:    list[tuple[str, int, int]] = []

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    async def on_llm_start(
        self,
        context: RunContextWrapper[Any],
        agent: Any,
        system_prompt: str | None,
        input_items: list[TResponseInputItem],
    ) -> None:
        self.calls += 1
        _emit(
            "llm-in",
            n=self.calls,
            input_items=len(input_items),
            input_text_chars=_input_text_chars(input_items),
        )

    async def on_llm_end(
        self,
        context: RunContextWrapper[Any],
        agent: Any,
        response: ModelResponse,
    ) -> None:
        in_t  = int(response.usage.input_tokens  or 0)
        out_t = int(response.usage.output_tokens or 0)
        delta_in = in_t - self.prev_input
        self.input_tokens  += in_t
        self.output_tokens += out_t
        self.prev_input     = in_t
        _emit(
            "llm-out",
            n=self.calls,
            input_tokens=in_t,
            output_tokens=out_t,
            delta_in=f"{'+' if delta_in >= 0 else ''}{delta_in}",
        )
        # Same enforcement as production BudgetHooks, so the smoke also
        # exercises the per-turn cap path when the cap is set low.
        if self.input_tokens + self.output_tokens >= settings.turn_token_cap:
            raise TurnTokenCapExceeded(
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
                cap=settings.turn_token_cap,
            )

    async def on_tool_end(
        self,
        context: RunContextWrapper[Any],
        agent: Any,
        tool: Any,
        result: str,
    ) -> None:
        name = getattr(tool, "name", "<unknown>")
        # The tool's args size isn't exposed on `on_tool_end`; capture only
        # the result size (which is what re-enters context on the next call).
        result_bytes = len(result) if isinstance(result, str) else len(str(result))
        self.tool_calls.append((name, 0, result_bytes))
        _emit("tool", name=name, result_bytes=result_bytes)


async def _drive() -> int:
    host = urlparse(settings.provider_base_url).hostname or "unknown"
    _emit(
        "start",
        question=QUESTION,
        model=settings.model_name,
        provider_host=host,
        turn_token_cap=settings.turn_token_cap,
        max_turns=settings.max_turns_per_run,
    )

    hooks = MonitoringHooks()
    agent = build_agent()
    started = time.perf_counter()

    try:
        result = Runner.run_streamed(
            agent,
            input=QUESTION,
            hooks=hooks,
            max_turns=settings.max_turns_per_run,
        )
        # Consume the event stream so the run actually progresses. We don't
        # render deltas here; the hooks already emit per-boundary lines.
        async for _ in result.stream_events():
            pass
        final_output = (result.final_output or "").strip().replace("\n", " ")
    except TurnTokenCapExceeded as exc:
        _emit("cap-hit", scope="turn", reason=str(exc))
        final_output = f"<turn capped: {exc}>"
    except Exception as exc:
        _emit("error", type=type(exc).__name__, message=str(exc))
        return 1

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    cost, _ = compute_cost_usd(
        settings.model_name,
        {"input_tokens": hooks.input_tokens, "output_tokens": hooks.output_tokens},
    )

    # Persist to the budget store the same way transport.py does, so the
    # smoke also writes a session/global row a human can inspect.
    if settings.budget_enabled:
        budgets.init_budgets()
        budgets.record_usage(
            session_id="smoke-session",
            hashed_id="smoke-client",
            input_tokens=hooks.input_tokens,
            output_tokens=hooks.output_tokens,
        )

    _emit(
        "done",
        calls=hooks.calls,
        tool_calls=len(hooks.tool_calls),
        input_total=hooks.input_tokens,
        output_total=hooks.output_tokens,
        cost_usd=f"{cost:.6f}",
        elapsed_ms=elapsed_ms,
    )
    _emit("answer", text=final_output or "<empty>")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_drive()))
