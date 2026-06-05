"""Map runtime output onto assistant-ui state parts."""

from __future__ import annotations

from typing import Any

from assistant_stream import RunController


class StateEmitter:
    """Maps neutral runtime output onto assistant-ui native message parts."""

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

    def tool_result(self, call_id: str, result: object) -> None:
        idx = self._tool_idx_by_call.get(call_id)
        if idx is not None:
            self._parts()[idx]["result"] = result
            self._parts()[idx]["done"] = True

