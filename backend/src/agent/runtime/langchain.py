"""LangChain runtime adapter (ADR-0012, AGENT_RUNTIME=langchain).

Implements the `AgentRuntime` port over the LangChain stack. The harness is the
lean `langchain.agents.create_agent` with exactly one tool (`compare`), mirroring
the OpenAI Agents agent one-for-one so the two runtimes are a fair A/B: same
single tool, same citation prompt, honest per-turn token accounting.
All LangChain types stay inside this module. Transport sees only `Turn`,
`Emitter`, `RunUsage`, and `TurnTokenCapExceeded`.

Mapping (verified against langchain 1.3 / langgraph 1.2):
- `astream(..., stream_mode=["messages","updates"])`.
- `messages` mode -> `AIMessageChunk`: stream text via `emit.text_delta`.
- `updates` model node -> `AIMessage.tool_calls`: `emit.tool_call`.
- `updates` tools node -> `ToolMessage`: `emit.tool_result` with the `.artifact`
  dict (the tool returns content_and_artifact, so the structured result reaches
  the frontend intact while the model reads the JSON in `content`).
- Per-turn token cap and usage accounting ride on a `CapMiddleware.after_model`
  hook reading `usage_metadata`; the adapter mirrors its totals into `RunUsage`
  in a `finally` so a turn aborted by the cap still reports what it spent.
"""

from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI

from app_config import settings
from agent.runtime.types import Emitter, RunUsage, Turn, TurnTokenCapExceeded
from agent.runtime.prompt import INSTRUCTIONS
from agent.tools.pricing import (
    COMPARE_DESCRIPTION,
    CompareToolArgs,
    run_compare_for_model,
)


def _compare_tool() -> StructuredTool:
    """Bind the neutral `run_compare` as a LangChain tool. `content_and_artifact`
    puts the JSON the model reads (to cite from) in the message content and the
    structured dict the frontend renders in the artifact."""

    def compare(
        vcpu: int,
        ram_gb: float,
        region: str,
        family: str = "any",
        providers: list[str] | None = None,
        expand: str = "cheapest",
    ) -> tuple[str, dict[str, Any]]:
        return run_compare_for_model(
            vcpu=vcpu,
            ram_gb=ram_gb,
            region=region,
            family=family,
            providers=providers,
            expand=expand,
        )

    return StructuredTool.from_function(
        compare,
        name="compare",
        description=COMPARE_DESCRIPTION,
        args_schema=CompareToolArgs,
        response_format="content_and_artifact",
    )


def _build_model() -> ChatOpenAI:
    missing = [
        name
        for name, value in (
            ("PROVIDER_BASE_URL", settings.provider_base_url),
            ("PROVIDER_API_KEY", settings.provider_api_key),
            ("MODEL_NAME", settings.model_name),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "agent model is not configured: set "
            + ", ".join(missing)
            + " in .env (see .env.example)."
        )

    kwargs: dict[str, Any] = dict(
        base_url=settings.provider_base_url,
        api_key=settings.provider_api_key,
        model=settings.model_name,
        stream_usage=True,
    )
    if settings.langchain_reasoning_roundtrip:
        from agent.runtime.reasoning_model import ReasoningRoundTripChatOpenAI

        return ReasoningRoundTripChatOpenAI(**kwargs)
    return ChatOpenAI(**kwargs)


class CapMiddleware(AgentMiddleware):
    """Per-turn token cap (ADR-0011 seam [5]) for the langchain runtime, the
    analog of the Agents SDK `BudgetHooks`. Accumulates `usage_metadata` after
    every model call and raises the neutral `TurnTokenCapExceeded` once the
    cumulative total crosses the cap. Counts on `self`, one instance per run."""

    def __init__(self, cap: int) -> None:
        super().__init__()
        self.cap = cap
        self.input_tokens = 0
        self.output_tokens = 0

    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        messages = state.get("messages") if isinstance(state, dict) else None
        last = messages[-1] if messages else None
        usage = getattr(last, "usage_metadata", None)
        if usage:
            self.input_tokens += int(usage.get("input_tokens") or 0)
            self.output_tokens += int(usage.get("output_tokens") or 0)
        if self.input_tokens + self.output_tokens >= self.cap:
            raise TurnTokenCapExceeded(
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
                cap=self.cap,
            )
        return None


class LangChainRuntime:
    """`AgentRuntime` implementation backed by langchain's `create_agent`."""

    async def run(self, turns: list[Turn], emit: Emitter, usage: RunUsage) -> None:
        cap = CapMiddleware(settings.turn_token_cap)
        # list[Any]: create_agent wants a homogeneous Sequence[AgentMiddleware[...]],
        # but the two middlewares carry different generic params, which the type
        # checker will not unify. They are both AgentMiddleware at runtime.
        middleware: list[Any] = [
            cap,
            ModelCallLimitMiddleware(
                run_limit=settings.max_turns_per_run, exit_behavior="end"
            ),
        ]
        agent = create_agent(
            model=_build_model(),
            tools=[_compare_tool()],
            system_prompt=INSTRUCTIONS,
            middleware=middleware,
        )
        # LangChain's create_agent returns a graph whose `astream(input=...)`
        # type references a private `_InputAgentState`. The runtime contract is
        # the public messages-state shape below; keep the private type out of
        # our app code and type this adapter boundary as Any.
        agent_input: Any = {
            "messages": [{"role": t.role, "content": t.content} for t in turns]
        }
        try:
            async for mode, chunk in agent.astream(
                input=agent_input, stream_mode=["messages", "updates"]
            ):
                if mode == "messages":
                    message, _meta = chunk
                    if isinstance(message, AIMessageChunk):
                        # `.text` is a property in langchain-core 1.x (the old
                        # `.text()` method is deprecated).
                        text = message.text or ""
                        if text:
                            emit.text_delta(text)
                elif mode == "updates":
                    self._emit_updates(chunk, emit)
        finally:
            usage.input_tokens = cap.input_tokens
            usage.output_tokens = cap.output_tokens

    @staticmethod
    def _emit_updates(chunk: Any, emit: Emitter) -> None:
        if not isinstance(chunk, dict):
            return
        for update in chunk.values():
            messages = update.get("messages", []) if isinstance(update, dict) else []
            for message in messages:
                if isinstance(message, ToolMessage):
                    result = (
                        message.artifact
                        if message.artifact is not None
                        else message.content
                    )
                    if message.tool_call_id:
                        emit.tool_result(message.tool_call_id, result)
                elif isinstance(message, AIMessage):
                    for call in message.tool_calls or []:
                        args = call.get("args") or {}
                        emit.tool_call(
                            call.get("id") or "",
                            call.get("name") or "",
                            json.dumps(args),
                            args,
                        )
