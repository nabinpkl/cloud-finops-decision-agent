"""The assistant-transport endpoint: POST /assistant (ADR-0009).

The frontend (`web/`, assistant-ui's useAssistantTransportRuntime) POSTs commands
plus the prior conversation state; the backend streams state updates back in
assistant-stream format. State round-trips on every request, so the server holds
no session.

Stage A (this file as it stands) answers with a static reply that exercises the
full transport path: it streams assistant text and emits a tool-call part, so the
frontend rendering chain can be verified without a model or provider credentials.
Stage B replaces the static run_callback with the OpenAI Agents SDK bridge
(`Runner.run_streamed()` events -> controller state) and the real compare tool.

Messages use assistant-ui's native shape, which the frontend converter consumes:
  {"role": ..., "parts": [
      {"type": "text", "text": ...},
      {"type": "tool-call", "toolCallId", "toolName", "argsText", "args", "result", "done"},
  ]}
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Annotated, Any, Literal

from assistant_stream import RunController, create_run
from assistant_stream.serialization import DataStreamResponse
from fastapi import APIRouter
from pydantic import BaseModel, Field


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


@router.post("/assistant")
async def assistant_endpoint(request: AssistantRequest) -> DataStreamResponse:
    async def run_callback(controller: RunController) -> None:
        # Persist the user's just-sent command into the round-tripped state.
        cmd = request.commands[0]
        if isinstance(cmd, AddMessageCommand):
            controller.state["messages"].append(cmd.message.model_dump(exclude_none=True))
        elif isinstance(cmd, AddToolResultCommand):
            controller.state["messages"][-1]["parts"][-1]["result"] = cmd.result
            controller.state["messages"][-1]["parts"][-1]["done"] = True

        # Static assistant turn: a tool-call part, then streamed summary text.
        # Re-index from controller.state on each mutation so every change is
        # emitted (the reactive state tracks indexed writes, not held refs).
        controller.state["messages"].append({"role": "assistant", "parts": []})
        msg = len(controller.state["messages"]) - 1

        args = {"vcpu": 4, "ram_gb": 8, "region": "eu-central", "family": "general-purpose"}
        controller.state["messages"][msg]["parts"].append(
            {
                "type": "tool-call",
                "toolCallId": f"stub_{uuid.uuid4().hex}",
                "toolName": "compare",
                "argsText": json.dumps(args),
                "args": args,
                "done": False,
            }
        )
        await asyncio.sleep(0.3)
        controller.state["messages"][msg]["parts"][0]["result"] = {
            "note": "Stage A stub; the real compare tool lands in Stage B.",
        }
        controller.state["messages"][msg]["parts"][0]["done"] = True

        controller.state["messages"][msg]["parts"].append({"type": "text", "text": ""})
        reply = (
            "Stage A: the assistant-transport bridge is live. Streamed text and a "
            "tool-call part both render. The real compare tool wires in next."
        )
        text = ""
        for token in reply.split(" "):
            text = f"{text} {token}".strip()
            controller.state["messages"][msg]["parts"][1]["text"] = text
            await asyncio.sleep(0.02)

    incoming_state = request.state or {"messages": []}
    incoming_state.setdefault("messages", [])
    stream = create_run(run_callback, state=incoming_state)
    return DataStreamResponse(stream)
