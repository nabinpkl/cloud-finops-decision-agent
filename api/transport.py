"""The assistant-transport endpoint: POST /assistant (ADR-0009).

The frontend (`web/`, assistant-ui's useAssistantTransportRuntime) POSTs commands
plus the prior conversation state; the backend streams state updates back in
assistant-stream format. State round-trips on every request, so the server holds
no session.

Stage B (this file) runs the OpenAI Agents SDK in-process via
`Runner.run_streamed()` and bridges its events into reactive controller state:
text deltas grow a text part token by token, function-tool calls and outputs
append a `tool-call` part and fill in its result. The `compare` tool is wired in
`api/tools.py` and registered on the Agent in `api/agent.py`. The agent never
re-enters its own HTTP surface; the tool calls `normalize.query.compare`
directly in-process (ADR-0009).

Messages use assistant-ui's native shape, which the frontend converter consumes:
  {"role": ..., "parts": [
      {"type": "text", "text": ...},
      {"type": "tool-call", "toolCallId", "toolName", "argsText", "args", "result", "done"},
  ]}

Cross-turn input passed to the agent is text-only for v0: the assistant message
history is reconstructed by concatenating each message's text parts. Tool-call
parts from prior turns are not replayed; the next turn's tool calls are fresh.
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any, Literal, cast

from agents import (
    RawResponsesStreamEvent,
    RunItemStreamEvent,
    Runner,
    ToolCallItem,
    ToolCallOutputItem,
)
from agents.items import TResponseInputItem
from assistant_stream import RunController, create_run
from assistant_stream.serialization import DataStreamResponse
from fastapi import APIRouter
from openai.types.responses import ResponseTextDeltaEvent
from opentelemetry.trace import Status, StatusCode
from pydantic import BaseModel, Field

from api.agent import build_agent
from api.observability import get_tracer


class MessagePart(BaseModel):
    type: str
    text: str | None = None


class UserMessage(BaseModel):
    role: str = "user"
    parts: list[MessagePart]


class AddMessageCommand(BaseModel):
    type: Literal["add-message"] = "add-message"
    message: UserMessage


class AddToolResultCommand(BaseModel):
    type: Literal["add-tool-result"] = "add-tool-result"
    toolCallId: str
    result: dict[str, Any]


Command = Annotated[
    AddMessageCommand | AddToolResultCommand, Field(discriminator="type")
]


class AssistantRequest(BaseModel):
    commands: list[Command]
    system: str | None = None
    tools: dict[str, Any] | None = None
    runConfig: dict[str, Any] | None = None
    state: dict[str, Any] | None = None


router = APIRouter()


def _message_text(message: dict[str, Any]) -> str:
    """Concatenate all text parts of a message (v0 cross-turn input is text-only)."""
    return "".join(
        (part.get("text") or "")
        for part in message.get("parts", [])
        if part.get("type") == "text"
    ).strip()


def _raw_field(raw_item: Any, name: str) -> str:
    """ToolCallItem.raw_item is either a pydantic model or a dict; read either."""
    if isinstance(raw_item, dict):
        value = raw_item.get(name)
    else:
        value = getattr(raw_item, name, None)
    return value or ""


@router.post("/assistant")
async def assistant_endpoint(request: AssistantRequest) -> DataStreamResponse:
    async def run_callback(controller: RunController) -> None:
        # Apply every incoming command to the round-tripped state first. Reactive
        # writes go through controller.state[...]=... not held local refs.
        triggered_by_user_message = False
        for cmd in request.commands:
            if isinstance(cmd, AddMessageCommand):
                controller.state["messages"].append(
                    cmd.message.model_dump(exclude_none=True)
                )
                if cmd.message.role == "user":
                    triggered_by_user_message = True
            elif isinstance(cmd, AddToolResultCommand):
                # Frontend-supplied tool result (no v0 flow uses this, but keep
                # the handling so a future human-confirm tool slots in).
                msgs = controller.state["messages"]
                if msgs:
                    last_idx = len(msgs) - 1
                    for i, part in enumerate(msgs[last_idx].get("parts", [])):
                        if part.get("toolCallId") == cmd.toolCallId:
                            controller.state["messages"][last_idx]["parts"][i][
                                "result"
                            ] = cmd.result
                            controller.state["messages"][last_idx]["parts"][i][
                                "done"
                            ] = True
                            break

        if not triggered_by_user_message:
            return

        # Reconstruct the SDK input from prior state messages (text-only across
        # turns for v0). The new user message is already appended above.
        sdk_input: list[dict[str, str]] = []
        for m in controller.state["messages"]:
            text = _message_text(m)
            if text and m.get("role") in ("user", "assistant", "system"):
                sdk_input.append({"role": m["role"], "content": text})
        if not sdk_input:
            return

        # Allocate the assistant turn; parts append as events arrive.
        controller.state["messages"].append({"role": "assistant", "parts": []})
        msg = len(controller.state["messages"]) - 1
        tool_part_by_call_id: dict[str, int] = {}

        def _append_part(part: dict[str, Any]) -> int:
            controller.state["messages"][msg]["parts"].append(part)
            return len(controller.state["messages"][msg]["parts"]) - 1

        def _ensure_text_part() -> int:
            parts = controller.state["messages"][msg]["parts"]
            if parts and parts[-1].get("type") == "text":
                return len(parts) - 1
            return _append_part({"type": "text", "text": ""})

        tracer = get_tracer()
        history_text_length = sum(len(item["content"]) for item in sdk_input)
        last_user_length = len(sdk_input[-1]["content"]) if sdk_input else 0
        with tracer.start_as_current_span("agent.turn") as turn_span:
            turn_span.set_attribute("finops.user_message.length", last_user_length)
            turn_span.set_attribute("finops.cross_turn_history.message_count", len(sdk_input))
            turn_span.set_attribute("finops.cross_turn_history.text_length", history_text_length)
            # build_agent() inside the span so a missing-credentials failure is
            # captured by record_exception/set_status, not swallowed as a 500.
            agent = build_agent()
            result = Runner.run_streamed(
                agent, input=cast(list[TResponseInputItem], sdk_input)
            )
            try:
                async for event in result.stream_events():
                    if isinstance(event, RawResponsesStreamEvent):
                        data = event.data
                        if isinstance(data, ResponseTextDeltaEvent) and data.delta:
                            idx = _ensure_text_part()
                            current = (
                                controller.state["messages"][msg]["parts"][idx].get("text")
                                or ""
                            )
                            controller.state["messages"][msg]["parts"][idx]["text"] = (
                                current + data.delta
                            )
                    elif isinstance(event, RunItemStreamEvent):
                        item = event.item
                        if event.name == "tool_called" and isinstance(item, ToolCallItem):
                            call_id = item.call_id or f"call_{uuid.uuid4().hex}"
                            args_text = _raw_field(item.raw_item, "arguments")
                            try:
                                args_obj: dict[str, Any] = (
                                    json.loads(args_text) if args_text else {}
                                )
                            except json.JSONDecodeError:
                                args_obj = {}
                            idx = _append_part(
                                {
                                    "type":       "tool-call",
                                    "toolCallId": call_id,
                                    "toolName":   _raw_field(item.raw_item, "name"),
                                    "argsText":   args_text,
                                    "args":       args_obj,
                                    "done":       False,
                                }
                            )
                            tool_part_by_call_id[call_id] = idx
                        elif event.name == "tool_output" and isinstance(
                            item, ToolCallOutputItem
                        ):
                            call_id = item.call_id
                            if call_id and call_id in tool_part_by_call_id:
                                idx = tool_part_by_call_id[call_id]
                                controller.state["messages"][msg]["parts"][idx][
                                    "result"
                                ] = item.output
                                controller.state["messages"][msg]["parts"][idx][
                                    "done"
                                ] = True
            except Exception as exc:
                # Surface the failure in the visible thread so the user sees why
                # the turn stopped; the receipt remains the citation contract.
                turn_span.record_exception(exc)
                turn_span.set_status(Status(StatusCode.ERROR, str(exc)))
                _append_part(
                    {"type": "text", "text": f"\n\n[agent error: {type(exc).__name__}: {exc}]"}
                )
                raise

    incoming_state = request.state or {"messages": []}
    incoming_state.setdefault("messages", [])
    stream = create_run(run_callback, state=incoming_state)
    return DataStreamResponse(stream)
