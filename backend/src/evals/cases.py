from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_CASES_PATH = Path("evals/cases/v0.jsonl")


@dataclass(frozen=True)
class EvalCase:
    id: str
    user: str
    tool_call: dict[str, Any]
    tool_result: dict[str, Any]
    final_answer: str
    checks: list[str]
    expect: dict[str, Any] = field(default_factory=dict)


def load_cases(path: Path = DEFAULT_CASES_PATH) -> list[EvalCase]:
    cases: list[EvalCase] = []
    with path.open(encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            data = json.loads(stripped)
            cases.append(_case_from_dict(data, line_number=line_number))
    return cases


def _case_from_dict(data: dict[str, Any], *, line_number: int) -> EvalCase:
    missing = {
        key
        for key in ("id", "user", "tool_call", "tool_result", "final_answer", "checks")
        if key not in data
    }
    if missing:
        joined = ", ".join(sorted(missing))
        raise ValueError(f"eval case line {line_number} missing required field(s): {joined}")
    return EvalCase(
        id=str(data["id"]),
        user=str(data["user"]),
        tool_call=dict(data["tool_call"]),
        tool_result=dict(data["tool_result"]),
        final_answer=str(data["final_answer"]),
        checks=list(data["checks"]),
        expect=dict(data.get("expect") or {}),
    )
