from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.cases import DEFAULT_CASES_PATH, load_cases
from evals.graders import CheckResult, grade_case
from evals.identity import build_eval_identity
from evals.replay import replay_case


@dataclass(frozen=True)
class TrialRun:
    case_id: str
    kind: str
    source: str
    rail: str
    lane: str
    trial_index: int
    trial_count: int
    checks: list[CheckResult]
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_ms: float | None = None
    tool_call_count: int | None = None

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "kind": self.kind,
            "source": self.source,
            "rail": self.rail,
            "lane": self.lane,
            "trial_index": self.trial_index,
            "trial_count": self.trial_count,
            "passed": self.passed,
            "usage": {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
            },
            "elapsed_ms": self.elapsed_ms,
            "tool_call_count": self.tool_call_count,
            "checks": [
                {
                    "name": check.name,
                    "passed": check.passed,
                    "detail": check.detail,
                    "failure_label": check.failure_label,
                }
                for check in self.checks
            ],
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run offline agent contract evals.")
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES_PATH,
        help="YAML eval suite file or directory.",
    )
    parser.add_argument(
        "--mode",
        choices=("transcript", "replay", "both"),
        default="both",
        help=(
            "Eval lane to run. transcript grades YAML directly; replay emits "
            "through the runtime port first."
        ),
    )
    parser.add_argument(
        "--trials",
        type=int,
        default=1,
        help="Minimum number of trials per case/lane. Reports pass^k.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Optional compact JSON report path.",
    )
    args = parser.parse_args(argv)
    if args.trials < 1:
        parser.error("--trials must be >= 1")

    cases = load_cases(args.cases)
    runs: list[TrialRun] = []
    total_checks = 0
    for case in cases:
        trial_count = max(case.trial_count or 1, args.trials)
        if args.mode in {"transcript", "both"}:
            case_runs = [
                TrialRun(
                    case_id=case.id,
                    kind=case.kind,
                    source=case.source,
                    rail=case.rail,
                    lane="transcript",
                    trial_index=trial_index,
                    trial_count=trial_count,
                    checks=grade_case(case),
                )
                for trial_index in range(1, trial_count + 1)
            ]
            _print_case_results(case.id, "transcript", case_runs)
            total_checks += sum(len(run.checks) for run in case_runs)
            runs.extend(case_runs)
        if args.mode in {"replay", "both"}:
            case_runs = []
            for trial_index in range(1, trial_count + 1):
                replayed = replay_case(case)
                case_runs.append(
                    TrialRun(
                        case_id=case.id,
                        kind=case.kind,
                        source=case.source,
                        rail=case.rail,
                        lane="replay",
                        trial_index=trial_index,
                        trial_count=trial_count,
                        checks=replayed.checks,
                        input_tokens=replayed.usage.input_tokens,
                        output_tokens=replayed.usage.output_tokens,
                        elapsed_ms=replayed.elapsed_ms,
                        tool_call_count=replayed.tool_call_count,
                    )
                )
            _print_case_results(case.id, "replay", case_runs)
            total_checks += sum(len(run.checks) for run in case_runs)
            runs.extend(case_runs)

    lane_count = 2 if args.mode == "both" else 1
    failed = sum(
        1
        for case_id, lane in {(run.case_id, run.lane) for run in runs}
        if not all(run.passed for run in runs if run.case_id == case_id and run.lane == lane)
    )
    print(
        f"\n{len(cases)} case(s), {lane_count} lane(s), "
        f"{total_checks} check(s), {failed} failed pass^k case/lane(s)"
    )
    if args.report is not None:
        identity = build_eval_identity(args.cases)
        _write_report(args.report, runs, identity.to_dict())
        prompts = identity.prompts
        model_config = identity.model_config
        cases_identity = identity.cases
        print(
            "report identity: "
            f"price_agent_prompt={prompts['price_agent']['rendered_sha256']} "
            f"input_judge_prompt={prompts['input_judge']['rendered_sha256']} "
            f"model_config={model_config['sha256']} "
            f"cases={cases_identity['sha256']}"
        )
    return 1 if failed else 0


def _print_case_results(case_id: str, lane: str, runs: list[TrialRun]) -> None:
    passed = all(run.passed for run in runs)
    status = "PASS" if passed else "FAIL"
    print(f"{status} {case_id} [{lane} pass^{len(runs)}]")
    for run in runs:
        if len(runs) > 1:
            print(f"  trial {run.trial_index}/{run.trial_count}")
        indent = "    " if len(runs) > 1 else "  "
        for result in run.checks:
            marker = "ok" if result.passed else "not ok"
            label = f" [{result.failure_label}]" if result.failure_label else ""
            print(f"{indent}{marker} {result.name}{label}: {result.detail}")


def _write_report(path: Path, runs: list[TrialRun], identity: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 2,
        "identity": identity,
        "trial_runs": [run.to_dict() for run in runs],
        "case_lane_results": [
            {
                "case_id": case_id,
                "lane": lane,
                "trial_count": len(group),
                "passed": all(run.passed for run in group),
            }
            for case_id, lane in sorted({(run.case_id, run.lane) for run in runs})
            for group in [[run for run in runs if run.case_id == case_id and run.lane == lane]]
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
