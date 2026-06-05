"""assistant-ui state mutation helpers."""

from __future__ import annotations

from typing import Any

from api.assistant_transport.models import (
    AddMessageCommand,
    AddToolResultCommand,
    Command,
)
from api.runtime import Turn

SESSION_LIMIT_MESSAGE = (
    "This conversation reached its token limit. "
    "Start a new conversation to continue."
)


def prepare_incoming_state(raw_state: dict[str, Any] | None) -> dict[str, Any]:
    state = raw_state or {"messages": []}
    state.setdefault("messages", [])
    # Round-tripped state is UI scaffolding. Enforcement flags are backend-owned.
    state.pop("sessionLimitReached", None)
    return state


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
        text = _message_text(message)
        if text and message.get("role") in ("user", "assistant", "system"):
            turns.append(Turn(role=message["role"], content=text))
    return turns


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
    return "".join(
        (part.get("text") or "")
        for part in message.get("parts", [])
        if part.get("type") == "text"
    ).strip()

