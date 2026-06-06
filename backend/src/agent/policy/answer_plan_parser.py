"""Parsing entry points for model-emitted pricing answer plans."""

from __future__ import annotations

import json
from typing import Any, cast

from pydantic import ValidationError

from agent.policy.answer_plan_models import AnswerPlan
from agent.policy.answer_plan_rendering import render_answer_plan
from agent.policy.answer_plan_validation import validate_answer_plan
from agent.policy.final_answer import PolicyViolation


def parse_answer_plan(text: str) -> tuple[AnswerPlan | None, list[PolicyViolation]]:
    try:
        raw = json.loads(_strip_json_markdown(text))
    except json.JSONDecodeError as exc:
        return None, [PolicyViolation("answer_plan_parse", f"invalid JSON: {exc.msg}")]
    try:
        return AnswerPlan.model_validate(raw), []
    except ValidationError as exc:
        return None, [PolicyViolation("answer_plan_schema", str(exc).splitlines()[0])]


def render_checked_answer_plan(
    text: str,
    tool_results: list[object],
) -> tuple[str | None, list[PolicyViolation]]:
    plan, violations = parse_answer_plan(text)
    if violations or plan is None:
        return None, violations
    normalized_results = [_coerce_tool_result(result) for result in tool_results]
    violations = validate_answer_plan(plan, normalized_results)
    if violations:
        return None, violations
    return render_answer_plan(plan), []


def _coerce_tool_result(result: object) -> dict[str, Any]:
    if isinstance(result, dict):
        return cast(dict[str, Any], result)
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError:
            return {}
        return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else {}
    return {}


def _strip_json_markdown(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned
