"""Single-turn live smoke against the real model, driven through the runtime
port so it exercises whichever runtime `AGENT_RUNTIME` selects (ADR-0012).
Bypasses HTTP and the frontend so the output isolates the agent loop.

    uv run python -m scripts.agent_smoke
    AGENT_RUNTIME=deepagents just smoke
    AGENT_RUNTIME=deepagents LANGCHAIN_REASONING_ROUNDTRIP=true just smoke

Stdout shape (one line per event, parsed by Monitor or `tail -f`):

    [start]   question="..." runtime="..." model="..." provider_host="..."
    [tool-call] name=compare args_bytes=120
    [tool]    name=compare result_bytes=2400
    [done]    runtime=... input_total=4800 output_total=530 cost_usd=0.0042 ...
    [answer]  <assistant text on one line>

Token granularity differs by runtime (ADR-0012): the OpenAI Agents runtime
counts per-LLM-call, the langchain runtime per-step. The cumulative
`input_total`/`output_total` is the comparable figure; per-call deltas are not
surfaced here precisely because they would not line up across runtimes.

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

from api.budget_store import init_budgets, record_usage
from api.config import settings
from api.observability import compute_cost_usd
from api.runtime import RunUsage, Turn, TurnTokenCapExceeded, get_runtime

QUESTION = "Cheapest 4 vCPU 8 GB general-purpose VM in eu-central-1?"


def _emit(tag: str, **fields: Any) -> None:
    """Write one structured line to stdout, flushed immediately so background
    readers (Monitor, tail -f) see it in real time."""
    parts = [f"[{tag}]"]
    for key, value in fields.items():
        if isinstance(value, str) and (" " in value or "=" in value or '"' in value):
            escaped = value.replace('"', '\\"')
            parts.append(f'{key}="{escaped}"')
        else:
            parts.append(f"{key}={value}")
    print(" ".join(parts), flush=True)


class ConsoleEmitter:
    """An `Emitter` that prints one tagged line per tool boundary and buffers
    assistant text for a single `[answer]` line at the end. Runtime-agnostic:
    the same lines come out whichever runtime produced the events."""

    def __init__(self) -> None:
        self.text_parts: list[str] = []
        self.tool_calls = 0
        self.tool_results = 0

    def text_delta(self, text: str) -> None:
        self.text_parts.append(text)

    def tool_call(self, call_id: str, name: str, args_text: str, args: dict) -> None:
        self.tool_calls += 1
        _emit("tool-call", name=name or "<unknown>", args_bytes=len(args_text or ""))

    def tool_result(self, call_id: str, result: object) -> None:
        self.tool_results += 1
        rendered = result if isinstance(result, str) else str(result)
        _emit("tool", call_id=call_id or "<none>", result_bytes=len(rendered))

    @property
    def answer(self) -> str:
        return "".join(self.text_parts).strip().replace("\n", " ")


async def _drive() -> int:
    host = urlparse(settings.provider_base_url).hostname or "unknown"
    _emit(
        "start",
        question=QUESTION,
        runtime=settings.agent_runtime,
        model=settings.model_name,
        provider_host=host,
        turn_token_cap=settings.turn_token_cap,
        max_turns=settings.max_turns_per_run,
    )

    emitter = ConsoleEmitter()
    usage = RunUsage()
    runtime = get_runtime()
    started = time.perf_counter()

    try:
        await runtime.run([Turn(role="user", content=QUESTION)], emitter, usage)
        final_output = emitter.answer
    except TurnTokenCapExceeded as exc:
        _emit("cap-hit", scope="turn", reason=str(exc))
        final_output = f"<turn capped: {exc}>"
    except Exception as exc:  # noqa: BLE001 - smoke surfaces any failure verbatim
        _emit("error", type=type(exc).__name__, message=str(exc))
        return 1

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    cost, _ = compute_cost_usd(
        settings.model_name,
        {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
    )

    # Persist to the budget store the same way transport.py does, so the smoke
    # also writes a session/global row a human can inspect.
    if settings.budget_enabled:
        init_budgets()
        record_usage(
            session_id="smoke-session",
            hashed_id="smoke-client",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )

    _emit(
        "done",
        runtime=settings.agent_runtime,
        tool_calls=emitter.tool_calls,
        input_total=usage.input_tokens,
        output_total=usage.output_tokens,
        cost_usd=f"{cost:.6f}",
        elapsed_ms=elapsed_ms,
    )
    _emit("answer", text=final_output or "<empty>")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_drive()))
