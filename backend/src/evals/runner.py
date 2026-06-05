from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evals.cases import DEFAULT_CASES_PATH, load_cases
from evals.graders import grade_case


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run offline agent contract evals.")
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES_PATH,
        help="JSONL eval case path.",
    )
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    failed = 0
    total_checks = 0
    for case in cases:
        results = grade_case(case)
        total_checks += len(results)
        case_failed = [result for result in results if not result.passed]
        status = "FAIL" if case_failed else "PASS"
        print(f"{status} {case.id}")
        for result in results:
            marker = "ok" if result.passed else "not ok"
            print(f"  {marker} {result.name}: {result.detail}")
        failed += len(case_failed)

    print(f"\n{len(cases)} case(s), {total_checks} check(s), {failed} failure(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
