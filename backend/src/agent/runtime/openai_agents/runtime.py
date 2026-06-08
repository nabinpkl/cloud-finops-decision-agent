"""OpenAI Agents SDK runtime adapter (ADR-0009, ADR-0012).

Implements the `AgentRuntime` port over the OpenAI Agents SDK: builds the agent,
runs one streamed turn, and translates the SDK's stream events onto the neutral
`Emitter` verbs. The per-turn token cap is enforced by `BudgetHooks`, which
raises the neutral `TurnTokenCapExceeded`;
this adapter mirrors the hook's running totals into the caller-owned `RunUsage`
in a `finally` so a turn aborted by the cap still reports what it spent.

All Agents-SDK types stay inside this module. Transport sees only `Turn`,
`Emitter`, `RunUsage`, and `TurnTokenCapExceeded`.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, cast

from agents import (
    RawResponsesStreamEvent,
    RunItemStreamEvent,
    Runner,
    ToolCallItem,
    ToolCallOutputItem,
)
from agents.items import TResponseInputItem
from openai.types.responses import ResponseTextDeltaEvent

from app_config import settings
from agent.security.untrusted import unwrap_tool_result_json
from agent.runtime.types import Emitter, RunUsage, Turn
from agent.runtime.openai_agents.agent import build_agent
from agent.runtime.openai_agents.hooks import BudgetHooks


def _raw_field(raw_item: Any, name: str) -> str:
    """ToolCallItem.raw_item is either a pydantic model or a dict; read either."""
    if isinstance(raw_item, dict):
        value = raw_item.get(name)
    else:
        value = getattr(raw_item, name, None)
    return value or ""


def _tool_output_artifact(output: object) -> object:
    if isinstance(output, str):
        parsed = unwrap_tool_result_json(output)
        if parsed is not None:
            return parsed
    return output


class OpenAIAgentsRuntime:
    """`AgentRuntime` implementation backed by the OpenAI Agents SDK."""

    async def run(self, turns: list[Turn], emit: Emitter, usage: RunUsage) -> None:
        sdk_input = [{"role": t.role, "content": t.content} for t in turns]
        hooks = BudgetHooks()
        agent = build_agent()
        result = Runner.run_streamed(
            agent,
            input=cast(list[TResponseInputItem], sdk_input),
            hooks=hooks,
            max_turns=settings.max_turns_per_run,
        )
        tool_call_ids: set[str] = set()
        try:
            async for event in result.stream_events():
                if isinstance(event, RawResponsesStreamEvent):
                    data = event.data
                    if isinstance(data, ResponseTextDeltaEvent) and data.delta:
                        emit.text_delta(data.delta)
                elif isinstance(event, RunItemStreamEvent):
                    item = event.item
                    if event.name == "tool_called" and isinstance(item, ToolCallItem):
                        call_id   = item.call_id or f"call_{uuid.uuid4().hex}"
                        args_text = _raw_field(item.raw_item, "arguments")
                        try:
                            args_obj: dict[str, Any] = (
                                json.loads(args_text) if args_text else {}
                            )
                        except json.JSONDecodeError:
                            args_obj = {}
                        tool_call_ids.add(call_id)
                        emit.tool_call(
                            call_id,
                            _raw_field(item.raw_item, "name"),
                            args_text,
                            args_obj,
                        )
                    elif event.name == "tool_output" and isinstance(
                        item, ToolCallOutputItem
                    ):
                        call_id = item.call_id
                        if call_id and call_id in tool_call_ids:
                            emit.tool_result(call_id, _tool_output_artifact(item.output))
        finally:
            # Mirror partial usage out even when stream_events() raised (the
            # cap exception or any other), so transport's finally can persist
            # what the turn consumed.
            usage.add(hooks.usage)
