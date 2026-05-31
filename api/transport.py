"""The assistant-transport endpoint: POST /assistant (ADR-0009, ADR-0011).

The frontend (`web/`, assistant-ui's useAssistantTransportRuntime) POSTs commands
plus the prior conversation state; the backend streams state updates back in
assistant-stream format. Per ADR-0009's amendment the round-tripped state is
**UI scaffolding only**; all enforcement state (per-session token totals,
cumulative spend, the cookie-keyed identity) lives server-side in
`api/budgets.py` and is read from a backend-set cookie, never from the body.

Per-turn flow:

1. The middleware (`api/middleware.py`) has already enforced the global daily
   cap and the per-client request-rate cap, and attached
   `request.state.hashed_client_id` for use here.
2. We read or set the `finops_session_id` cookie. The cookie name and TTL
   come from settings; it is `HttpOnly` and `SameSite=Lax`.
3. We check the per-session token cap. If exceeded we append a terminal
   assistant message and set `state.sessionLimitReached=true` so the frontend
   can render the "Start new conversation" banner; the agent is not invoked.
4. We run the agent under `BudgetHooks` (per-turn token cap) with
   `max_turns=settings.max_turns_per_run`.
5. In the `finally`, we persist the turn's usage via `budgets.record_usage`
   regardless of success or failure, so a partial run still pays for what it
   used.

Messages use assistant-ui's native shape, which the frontend converter
consumes:
  {"role": ..., "parts": [
      {"type": "text", "text": ...},
      {"type": "tool-call", "toolCallId", "toolName", "argsText", "args", "result", "done"},
  ]}
Cross-turn input passed to the agent is text-only for v0.
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
from fastapi import APIRouter, Request
from openai.types.responses import ResponseTextDeltaEvent
from opentelemetry.trace import Status, StatusCode
from pydantic import BaseModel, Field

from api import budgets
from api.agent import build_agent
from api.config import settings
from api.hooks import BudgetHooks, TurnTokenCapExceeded
from api.observability import get_tracer

SESSION_LIMIT_MESSAGE = (
    "This conversation reached its token limit. "
    "Start a new conversation to continue."
)


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
async def assistant_endpoint(
    body: AssistantRequest,
    request: Request,
) -> DataStreamResponse:
    # Identity: cookie session id is server-authoritative; the hashed client
    # id was computed by BudgetMiddleware. If the middleware was disabled
    # (settings.budget_enabled=False), the hashed id is absent and
    # record_usage is skipped for the client_window write.
    session_id = request.cookies.get(settings.session_cookie_name) or budgets.new_session_id()
    hashed_id  = getattr(request.state, "hashed_client_id", "") or ""

    async def run_callback(controller: RunController) -> None:
        triggered_by_user_message = False
        for cmd in body.commands:
            if isinstance(cmd, AddMessageCommand):
                controller.state["messages"].append(
                    cmd.message.model_dump(exclude_none=True)
                )
                if cmd.message.role == "user":
                    triggered_by_user_message = True
            elif isinstance(cmd, AddToolResultCommand):
                msgs = controller.state["messages"]
                if msgs:
                    last_idx = len(msgs) - 1
                    for i, part in enumerate(msgs[last_idx].get("parts", [])):
                        if part.get("toolCallId") == cmd.toolCallId:
                            controller.state["messages"][last_idx]["parts"][i]["result"] = cmd.result
                            controller.state["messages"][last_idx]["parts"][i]["done"]   = True
                            break

        if not triggered_by_user_message:
            return

        # Session cap check before any model work. The cap is server-trusted;
        # nothing in `body` can lower it. If hit, push the terminal message
        # and the state flag and return — the agent is not invoked, so no
        # tokens are consumed.
        block = budgets.check_session_cap(session_id) if settings.budget_enabled else None
        if block is not None:
            controller.state["messages"].append({
                "role": "assistant",
                "parts": [{"type": "text", "text": SESSION_LIMIT_MESSAGE}],
            })
            controller.state["sessionLimitReached"] = True
            return

        sdk_input: list[dict[str, str]] = []
        for m in controller.state["messages"]:
            text = _message_text(m)
            if text and m.get("role") in ("user", "assistant", "system"):
                sdk_input.append({"role": m["role"], "content": text})
        if not sdk_input:
            return

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

        usage_before = (
            budgets.read_session_usage(session_id)
            if settings.budget_enabled
            else budgets.SessionUsage(session_id=session_id, input_tokens=0, output_tokens=0)
        )
        hooks = BudgetHooks()

        tracer = get_tracer()
        history_text_length = sum(len(item["content"]) for item in sdk_input)
        last_user_length    = len(sdk_input[-1]["content"]) if sdk_input else 0
        with tracer.start_as_current_span("agent.turn") as turn_span:
            turn_span.set_attribute("finops.user_message.length",             last_user_length)
            turn_span.set_attribute("finops.cross_turn_history.message_count", len(sdk_input))
            turn_span.set_attribute("finops.cross_turn_history.text_length",   history_text_length)
            turn_span.set_attribute("finops.session.id_hash",                  budgets.session_id_fingerprint(session_id))
            turn_span.set_attribute("finops.session.tokens_before",            usage_before.total)
            turn_span.set_attribute("finops.session.budget_limit",             settings.session_token_cap)

            agent = build_agent()
            result = Runner.run_streamed(
                agent,
                input=cast(list[TResponseInputItem], sdk_input),
                hooks=hooks,
                max_turns=settings.max_turns_per_run,
            )
            try:
                async for event in result.stream_events():
                    if isinstance(event, RawResponsesStreamEvent):
                        data = event.data
                        if isinstance(data, ResponseTextDeltaEvent) and data.delta:
                            idx = _ensure_text_part()
                            current = (
                                controller.state["messages"][msg]["parts"][idx].get("text") or ""
                            )
                            controller.state["messages"][msg]["parts"][idx]["text"] = (
                                current + data.delta
                            )
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
                            idx = _append_part({
                                "type":       "tool-call",
                                "toolCallId": call_id,
                                "toolName":   _raw_field(item.raw_item, "name"),
                                "argsText":   args_text,
                                "args":       args_obj,
                                "done":       False,
                            })
                            tool_part_by_call_id[call_id] = idx
                        elif event.name == "tool_output" and isinstance(item, ToolCallOutputItem):
                            call_id = item.call_id
                            if call_id and call_id in tool_part_by_call_id:
                                idx = tool_part_by_call_id[call_id]
                                controller.state["messages"][msg]["parts"][idx]["result"] = item.output
                                controller.state["messages"][msg]["parts"][idx]["done"]   = True
            except TurnTokenCapExceeded as exc:
                turn_span.record_exception(exc)
                turn_span.set_status(Status(StatusCode.ERROR, str(exc)))
                turn_span.set_attribute("finops.budget.exhausted", True)
                turn_span.set_attribute("finops.budget.scope",     "turn")
                _append_part(
                    {"type": "text", "text": f"\n\n[turn stopped: {exc}]"}
                )
            except Exception as exc:
                turn_span.record_exception(exc)
                turn_span.set_status(Status(StatusCode.ERROR, str(exc)))
                _append_part(
                    {"type": "text", "text": f"\n\n[agent error: {type(exc).__name__}: {exc}]"}
                )
                raise
            finally:
                # Persist whatever the turn used, even on failure. Skipped
                # if the run was blocked pre-agent (no hooks counts) or if
                # the budget store is disabled.
                if settings.budget_enabled and (hooks.turn_input_tokens or hooks.turn_output_tokens):
                    budgets.record_usage(
                        session_id=session_id,
                        hashed_id=hashed_id,
                        input_tokens=hooks.turn_input_tokens,
                        output_tokens=hooks.turn_output_tokens,
                    )
                    turn_span.set_attribute(
                        "finops.session.tokens_after",
                        usage_before.total + hooks.turn_total_tokens,
                    )

    incoming_state = body.state or {"messages": []}
    incoming_state.setdefault("messages", [])
    # Strip any client-supplied enforcement field; per ADR-0009's amendment
    # the round-tripped state is UI scaffolding and the backend owns
    # `sessionLimitReached`. Removing it on the way in prevents a stuck
    # banner state from a client that lied.
    incoming_state.pop("sessionLimitReached", None)

    stream = create_run(run_callback, state=incoming_state)
    response = DataStreamResponse(stream)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_id,
        max_age=settings.session_idle_timeout_minutes * 60,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )
    return response
