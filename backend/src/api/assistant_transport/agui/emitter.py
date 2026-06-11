"""Map neutral runtime Emitter verbs onto AG-UI events + view-state.

``AGUIStateEmitter`` implements the framework-neutral ``Emitter`` protocol
(ADR-0012): the runtime adapters keep emitting ``text_delta`` / ``tool_call`` /
``tool_result`` and never learn about AG-UI. This single encoder maps those
verbs onto AG-UI wire events (``TEXT_MESSAGE_*``, ``TOOL_CALL_*``) for live
streaming, and mirrors the same content into the backend-authoritative
view-state (``ctx.state['messages']``) so a ``STATE_SNAPSHOT`` after the turn
carries the settled assistant message. The state shape is identical to the
previous transport, so the message helpers and ``PolicyEmitter`` wrapping are
unchanged.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from ag_ui.core import (
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)

from api.assistant_transport.agui.context import AGUIRunContext
from api.assistant_transport.view_state import apply_view_tool_result


def _pricing_result_rows(result: object) -> list[dict[str, Any]] | None:
    """Extract validated result rows from a compare/lookup tool result.

    Returns the row list when ``result`` looks like a pricing tool result
    (carries ``results`` and/or ``result``), else None so a non-pricing result
    (e.g. a set_view echo) does not clobber the binding target.
    """
    if not isinstance(result, dict):
        return None
    mapping = cast(dict[str, Any], result)
    if "results" not in mapping and "result" not in mapping:
        return None
    rows: list[dict[str, Any]] = []
    listed = mapping.get("results")
    if isinstance(listed, list):
        rows.extend(row for row in listed if isinstance(row, dict))
    single = mapping.get("result")
    if isinstance(single, dict):
        rows.append(single)
    return rows


class AGUIStateEmitter:
    """Emit AG-UI events and mirror content into the view-state message."""

    def __init__(self, ctx: AGUIRunContext, msg_index: int) -> None:
        self._ctx = ctx
        self._msg = msg_index
        self._message_id = f"msg_{uuid.uuid4().hex}"
        self._text_open = False
        self._tool_idx_by_call: dict[str, int] = {}
        # The latest validated compare/lookup rows: the binding target a
        # subsequent set_view/select result must reference. set_view can only
        # show rows that exist here (TASKS R3 / ADR-0016 HARD CONSTRAINT).
        self._latest_result_rows: list[dict[str, Any]] = []

    # --- view-state mirror (identical shape to the prior transport) ---------

    def _parts(self) -> list[dict[str, Any]]:
        return self._ctx.state["messages"][self._msg]["parts"]

    def _append_part(self, part: dict[str, Any]) -> int:
        self._parts().append(part)
        return len(self._parts()) - 1

    def _ensure_text_part(self) -> int:
        parts = self._parts()
        if parts and parts[-1].get("type") == "text":
            return len(parts) - 1
        return self._append_part({"type": "text", "text": ""})

    # --- neutral Emitter protocol -------------------------------------------

    def text_delta(self, text: str) -> None:
        idx = self._ensure_text_part()
        current = self._parts()[idx].get("text") or ""
        self._parts()[idx]["text"] = current + text

        if not self._text_open:
            self._ctx.emit_event(
                TextMessageStartEvent(message_id=self._message_id, role="assistant")
            )
            self._text_open = True
        self._ctx.emit_event(
            TextMessageContentEvent(message_id=self._message_id, delta=text)
        )

    def tool_call(self, call_id: str, name: str, args_text: str, args: dict) -> None:
        if self._text_open:
            self._ctx.emit_event(TextMessageEndEvent(message_id=self._message_id))
            self._text_open = False

        idx = self._append_part(
            {
                "type": "tool-call",
                "toolCallId": call_id,
                "toolName": name,
                "argsText": args_text,
                "args": args,
                "done": False,
            }
        )
        self._tool_idx_by_call[call_id] = idx

        self._ctx.emit_event(
            ToolCallStartEvent(
                tool_call_id=call_id,
                tool_call_name=name,
                parent_message_id=self._message_id,
            )
        )
        if args_text:
            self._ctx.emit_event(
                ToolCallArgsEvent(tool_call_id=call_id, delta=args_text)
            )
        self._ctx.emit_event(ToolCallEndEvent(tool_call_id=call_id))

    def tool_result(self, call_id: str, result: object) -> None:
        idx = self._tool_idx_by_call.get(call_id)
        if idx is not None:
            self._parts()[idx]["result"] = result
            self._parts()[idx]["done"] = True
        # Track the latest validated compare/lookup rows so a following
        # set_view/select result can be bound against them.
        rows = _pricing_result_rows(result)
        if rows is not None:
            self._latest_result_rows = rows
        # Co-driver tools mutate the backend-authoritative view-state. The
        # backend is the only writer; a set_view/select result lands in state
        # ONLY after registry + Tier-3-refusal + row-binding validation passes
        # against the latest validated result rows (ADR-0016 HARD CONSTRAINT).
        apply_view_tool_result(self._ctx.state, result, self._latest_result_rows)
        self._ctx.emit_event(
            ToolCallResultEvent(
                message_id=self._message_id,
                tool_call_id=call_id,
                content=_result_to_str(result),
            )
        )

    def close_text(self) -> None:
        """Close any open text message (called once the turn settles)."""
        if self._text_open:
            self._ctx.emit_event(TextMessageEndEvent(message_id=self._message_id))
            self._text_open = False


def _result_to_str(result: object) -> str:
    if isinstance(result, str):
        return result
    import json

    try:
        return json.dumps(result)
    except (TypeError, ValueError):
        return str(result)
