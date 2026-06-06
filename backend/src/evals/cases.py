from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CASES_PATH = PROJECT_ROOT / "evals/cases"


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    user: str
    tool_call: dict[str, Any]
    tool_result: dict[str, Any]
    answer_plan: dict[str, Any] | None = None
    final_answer: str
    checks: list[str]
    expect: dict[str, Any] = Field(default_factory=dict)


class EvalSuite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cases: list[EvalCase]


def load_cases(path: Path = DEFAULT_CASES_PATH) -> list[EvalCase]:
    suite_files = _suite_files(path)
    cases = [case for suite_file in suite_files for case in _load_suite(suite_file).cases]
    _reject_duplicate_ids(cases)
    return cases


def _suite_files(path: Path) -> list[Path]:
    if path.is_dir():
        files = sorted(path.glob("*.yaml")) + sorted(path.glob("*.yml"))
        if not files:
            raise ValueError(f"eval case directory has no YAML suites: {path}")
        return files
    if path.suffix not in {".yaml", ".yml"}:
        raise ValueError(f"eval case path must be a YAML file or directory: {path}")
    return [path]


def _load_suite(path: Path) -> EvalSuite:
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if raw is None:
        raise ValueError(f"eval suite is empty: {path}")
    if not isinstance(raw, dict):
        raise ValueError(f"eval suite must be a mapping with a cases list: {path}")
    return EvalSuite.model_validate(raw)


def _reject_duplicate_ids(cases: list[EvalCase]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for case in cases:
        if case.id in seen:
            duplicates.add(case.id)
        seen.add(case.id)
    if duplicates:
        joined = ", ".join(sorted(duplicates))
        raise ValueError(f"duplicate eval case id(s): {joined}")
