"""Emitter wrapper that validates claim plans before sending text to the UI."""

from __future__ import annotations

from agent.policy.answer_plan import render_checked_answer_plan
from agent.policy.final_answer import SAFE_FINAL_ANSWER, validate_final_answer
from agent.runtime import Emitter


class PolicyEmitter:
    """Forward tool events immediately, buffer model JSON until policy passes."""

    def __init__(self, inner: Emitter) -> None:
        self._inner = inner
        self._text_parts: list[str] = []
        self._tool_results: list[object] = []
        self.violations: list[str] = []

    @property
    def final_text(self) -> str:
        return "".join(self._text_parts)

    def text_delta(self, text: str) -> None:
        self._text_parts.append(text)

    def tool_call(self, call_id: str, name: str, args_text: str, args: dict) -> None:
        self._inner.tool_call(call_id, name, args_text, args)

    def tool_result(self, call_id: str, result: object) -> None:
        self._tool_results.append(result)
        self._inner.tool_result(call_id, result)

    def flush_checked(self) -> bool:
        rendered, violations = render_checked_answer_plan(
            self.final_text,
            self._tool_results,
        )
        if violations:
            self.violations = [f"{item.name}: {item.detail}" for item in violations]
            self._inner.text_delta(SAFE_FINAL_ANSWER)
            return False
        assert rendered is not None
        violations = validate_final_answer(rendered, self._tool_results)
        if violations:
            self.violations = [f"{item.name}: {item.detail}" for item in violations]
            self._inner.text_delta(SAFE_FINAL_ANSWER)
            return False
        self._inner.text_delta(rendered)
        return True

    def flush_unchecked(self) -> None:
        self._inner.text_delta(self.final_text)

    def discard_text(self) -> None:
        self._text_parts.clear()
