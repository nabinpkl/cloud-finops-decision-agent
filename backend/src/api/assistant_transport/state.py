"""assistant-ui state mutation helpers."""

from __future__ import annotations

from typing import Any

from api.assistant_transport.models import (
    AddMessageCommand,
    AddToolResultCommand,
    AGUIMessage,
    Command,
)
from agent.security.untrusted import wrap_assistant_history, wrap_user_request
from agent.runtime import Turn
from app_config import settings

SESSION_LIMIT_MESSAGE = (
    "This conversation reached its token limit. "
    "Start a new conversation to continue."
)


def default_view_state() -> dict[str, Any]:
    """The backend-authoritative view-state seed (ADR-0016 decision 3).

    The agent and the manual form both mutate this through the backend; the
    table renders ``view`` (declarative spec: columns/layout/grouping) and
    ``selection`` (annotations). Empty until a validated tool result populates
    it. The backend never trusts a client-supplied ``view``/``selection``;
    those are reset to defaults on every incoming request so the agent and
    deterministic layer remain the only writers of view-state.
    """
    return {"view": None, "selection": {"rows": [], "highlight": None}}


def prepare_incoming_state(raw_state: dict[str, Any] | None) -> dict[str, Any]:
    state = raw_state or {"messages": []}
    state.setdefault("messages", [])
    if not isinstance(state["messages"], list):
        state["messages"] = []
    state["messages"] = state["messages"][-settings.assistant_max_state_messages:]
    # Round-tripped state is UI scaffolding. Enforcement flags are backend-owned.
    state.pop("sessionLimitReached", None)
    # View-state is backend-authoritative: discard any client-supplied view or
    # selection and reseed from defaults. Only validated agent/form mutations
    # may write these fields.
    state.update(default_view_state())
    return state


def apply_agui_messages(
    state: dict[str, Any], messages: list[AGUIMessage]
) -> bool:
    """Seed conversation state from an AG-UI ``RunAgentInput.messages`` array.

    The AG-UI ``HttpAgent`` (the shipped frontend) sends the full conversation
    in ``messages`` rather than the legacy ``commands`` shape. Each AG-UI
    message carries ``{role, content}`` with ``content`` a plain string for
    user/assistant turns. We rebuild ``state['messages']`` in the internal
    parts shape so the same hardening surface (``build_turns`` XML wrapping,
    text/part caps, history cap) applies unchanged.

    Only ``user`` and ``assistant`` roles become turns; any client-supplied
    ``system``/``developer``/``tool`` message is dropped here exactly as the
    legacy path drops a non-user ``add-message`` (the agent's own system prompt
    is the only trusted instruction). Returns True when the latest turn is a
    user message — the signal that a new turn should run.
    """
    rebuilt: list[dict[str, Any]] = []
    for msg in messages[-settings.assistant_max_state_messages:]:
        if msg.role not in ("user", "assistant"):
            continue
        text = msg.content if isinstance(msg.content, str) else ""
        text = text[: settings.assistant_max_text_chars]
        rebuilt.append(
            {"role": msg.role, "parts": [{"type": "text", "text": text}]}
        )
    state["messages"] = rebuilt
    return bool(rebuilt) and rebuilt[-1]["role"] == "user"


def apply_commands(state: dict[str, Any], commands: list[Command]) -> bool:
    triggered_by_user_message = False
    for cmd in commands:
        if isinstance(cmd, AddMessageCommand):
            state["messages"].append(cmd.message.model_dump(exclude_none=True))
            if cmd.message.role == "user":
                triggered_by_user_message = True
        elif isinstance(cmd, AddToolResultCommand):
            _apply_tool_result(state, cmd)
    return triggered_by_user_message


def build_turns(state: dict[str, Any]) -> list[Turn]:
    turns: list[Turn] = []
    for message in state["messages"]:
        if not isinstance(message, dict):
            continue
        text = _message_text(message)
        role = message.get("role")
        if text and role == "user":
            turns.append(Turn(role="user", content=wrap_user_request(text)))
        elif text and role == "assistant":
            turns.append(Turn(role="assistant", content=wrap_assistant_history(text)))
    return turns


def history_text_length(state: dict[str, Any]) -> int:
    return sum(len(turn.content) for turn in build_turns(state))


def append_session_limit_message(state: dict[str, Any]) -> None:
    state["messages"].append(
        {
            "role": "assistant",
            "parts": [{"type": "text", "text": SESSION_LIMIT_MESSAGE}],
        }
    )
    state["sessionLimitReached"] = True


def append_assistant_message(state: dict[str, Any]) -> int:
    state["messages"].append({"role": "assistant", "parts": []})
    return len(state["messages"]) - 1


def _apply_tool_result(state: dict[str, Any], cmd: AddToolResultCommand) -> None:
    messages = state["messages"]
    if not messages:
        return
    last_idx = len(messages) - 1
    for i, part in enumerate(messages[last_idx].get("parts", [])):
        if part.get("toolCallId") == cmd.toolCallId:
            messages[last_idx]["parts"][i]["result"] = cmd.result
            messages[last_idx]["parts"][i]["done"] = True
            break


def _message_text(message: dict[str, Any]) -> str:
    """Concatenate all text parts of a message."""
    parts = message.get("parts", [])
    if not isinstance(parts, list):
        return ""
    text_parts: list[str] = []
    for part in parts[:settings.assistant_max_message_parts]:
        if not isinstance(part, dict) or part.get("type") != "text":
            continue
        text = part.get("text")
        if isinstance(text, str):
            text_parts.append(text[:settings.assistant_max_text_chars])
    return "".join(text_parts).strip()
