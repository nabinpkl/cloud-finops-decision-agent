"""The assistant-transport endpoint: POST /assistant (ADR-0009, ADR-0011).

The frontend (`web/`, assistant-ui's useAssistantTransportRuntime) POSTs commands
plus the prior conversation state; the backend streams state updates back in
assistant-stream format. Per ADR-0009's amendment the round-tripped state is
**UI scaffolding only**; all enforcement state (per-session token totals,
cumulative spend, the cookie-keyed identity) lives server-side in
`api/budget_store.py` and is read from a backend-set cookie, never from the body.

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
5. In the `finally`, we persist the turn's usage via `budget_store.record_usage`
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

from typing import Annotated, Any, Literal

from assistant_stream import RunController, create_run
from assistant_stream.serialization import DataStreamResponse
from fastapi import APIRouter, Request
from opentelemetry.trace import Status, StatusCode
from pydantic import BaseModel, Field

from api.budget_identity import new_session_id, session_id_fingerprint
from api.budget_models import SessionUsage
from api.budget_policy import check_session_cap
from api.budget_store import read_session_usage, record_usage
from api.config import settings
from api.observability import get_tracer
from api.runtime import RunUsage, Turn, TurnTokenCapExceeded, get_runtime

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


class _StateEmitter:
    """Maps a runtime's neutral output (`Emitter`) onto assistant-ui native
    parts on the round-tripped state. The runtime calls `text_delta`,
    `tool_call`, and `tool_result`; this class owns the part-building, so the
    wire shape stays in transport regardless of which framework produced the
    output. Mutation is in-place, matching what assistant_stream's
    `RunController` streams to the client."""

    def __init__(self, controller: RunController, msg_index: int) -> None:
        self._controller = controller
        self._msg = msg_index
        self._tool_idx_by_call: dict[str, int] = {}

    def _parts(self) -> list[dict[str, Any]]:
        return self._controller.state["messages"][self._msg]["parts"]

    def _append_part(self, part: dict[str, Any]) -> int:
        self._parts().append(part)
        return len(self._parts()) - 1

    def _ensure_text_part(self) -> int:
        parts = self._parts()
        if parts and parts[-1].get("type") == "text":
            return len(parts) - 1
        return self._append_part({"type": "text", "text": ""})

    def text_delta(self, text: str) -> None:
        idx = self._ensure_text_part()
        current = self._parts()[idx].get("text") or ""
        self._parts()[idx]["text"] = current + text

    def tool_call(self, call_id: str, name: str, args_text: str, args: dict) -> None:
        idx = self._append_part({
            "type":       "tool-call",
            "toolCallId": call_id,
            "toolName":   name,
            "argsText":   args_text,
            "args":       args,
            "done":       False,
        })
        self._tool_idx_by_call[call_id] = idx

    def tool_result(self, call_id: str, result: object) -> None:
        idx = self._tool_idx_by_call.get(call_id)
        if idx is not None:
            self._parts()[idx]["result"] = result
            self._parts()[idx]["done"]   = True


@router.post("/assistant")
async def assistant_endpoint(
    body: AssistantRequest,
    request: Request,
) -> DataStreamResponse:
    # Identity: cookie session id is server-authoritative; the hashed client
    # id was computed by BudgetMiddleware. If the middleware was disabled
    # (settings.budget_enabled=False), the hashed id is absent and
    # record_usage is skipped for the client_window write.
    session_id = request.cookies.get(settings.session_cookie_name) or new_session_id()
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
        block = check_session_cap(session_id) if settings.budget_enabled else None
        if block is not None:
            controller.state["messages"].append({
                "role": "assistant",
                "parts": [{"type": "text", "text": SESSION_LIMIT_MESSAGE}],
            })
            controller.state["sessionLimitReached"] = True
            return

        turns: list[Turn] = []
        for m in controller.state["messages"]:
            text = _message_text(m)
            if text and m.get("role") in ("user", "assistant", "system"):
                turns.append(Turn(role=m["role"], content=text))
        if not turns:
            return

        controller.state["messages"].append({"role": "assistant", "parts": []})
        msg = len(controller.state["messages"]) - 1
        emitter = _StateEmitter(controller, msg)

        usage_before = (
            read_session_usage(session_id)
            if settings.budget_enabled
            else SessionUsage(session_id=session_id, input_tokens=0, output_tokens=0)
        )
        run_usage = RunUsage()

        tracer = get_tracer()
        history_text_length = sum(len(t.content) for t in turns)
        last_user_length    = len(turns[-1].content) if turns else 0
        with tracer.start_as_current_span("agent.turn") as turn_span:
            turn_span.set_attribute("finops.user_message.length",             last_user_length)
            turn_span.set_attribute("finops.cross_turn_history.message_count", len(turns))
            turn_span.set_attribute("finops.cross_turn_history.text_length",   history_text_length)
            turn_span.set_attribute("finops.session.id_hash",                  session_id_fingerprint(session_id))
            turn_span.set_attribute("finops.session.tokens_before",            usage_before.total)
            turn_span.set_attribute("finops.session.budget_limit",             settings.session_token_cap)
            turn_span.set_attribute("finops.agent.runtime",                    settings.agent_runtime)

            # The runtime is chosen by AGENT_RUNTIME (ADR-0012). Transport
            # speaks only the neutral port: it hands over the turns and an
            # emitter, the runtime streams parts back and writes token usage
            # into `run_usage`, which is readable here even if the run aborts.
            runtime = get_runtime()
            try:
                await runtime.run(turns, emitter, run_usage)
            except TurnTokenCapExceeded as exc:
                turn_span.record_exception(exc)
                turn_span.set_status(Status(StatusCode.ERROR, str(exc)))
                turn_span.set_attribute("finops.budget.exhausted", True)
                turn_span.set_attribute("finops.budget.scope",     "turn")
                emitter.text_delta(f"\n\n[turn stopped: {exc}]")
            except Exception as exc:
                turn_span.record_exception(exc)
                turn_span.set_status(Status(StatusCode.ERROR, str(exc)))
                emitter.text_delta(
                    f"\n\n[agent error: {type(exc).__name__}: {exc}]"
                )
                raise
            finally:
                # Persist whatever the turn used, even on failure. Skipped
                # if the run was blocked pre-agent (no usage) or if the
                # budget store is disabled.
                if settings.budget_enabled and (run_usage.input_tokens or run_usage.output_tokens):
                    record_usage(
                        session_id=session_id,
                        hashed_id=hashed_id,
                        input_tokens=run_usage.input_tokens,
                        output_tokens=run_usage.output_tokens,
                    )
                    turn_span.set_attribute(
                        "finops.session.tokens_after",
                        usage_before.total + run_usage.total,
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
