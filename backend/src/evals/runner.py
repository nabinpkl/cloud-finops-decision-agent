from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evals.cases import DEFAULT_CASES_PATH, load_cases
from evals.graders import CheckResult, grade_case
from evals.replay import replay_case


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
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    failed = 0
    total_checks = 0
    for case in cases:
        if args.mode in {"transcript", "both"}:
            failed += _print_results(case.id, "transcript", grade_case(case))
            total_checks += len(case.checks)
        if args.mode in {"replay", "both"}:
            replayed = replay_case(case)
            failed += _print_results(case.id, "replay", replayed.checks)
            total_checks += len(replayed.checks)

    lane_count = 2 if args.mode == "both" else 1
    print(
        f"\n{len(cases)} case(s), {lane_count} lane(s), "
        f"{total_checks} check(s), {failed} failure(s)"
    )
    return 1 if failed else 0


def _print_results(case_id: str, lane: str, results: list[CheckResult]) -> int:
    case_failed = [result for result in results if not result.passed]
    status = "FAIL" if case_failed else "PASS"
    print(f"{status} {case_id} [{lane}]")
    for result in results:
        marker = "ok" if result.passed else "not ok"
        print(f"  {marker} {result.name}: {result.detail}")
    return len(case_failed)


if __name__ == "__main__":
    sys.exit(main())
