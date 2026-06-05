from __future__ import annotations

from evals.cases import EvalCase, load_cases
from evals.graders import grade_case


def _base_case(final_answer: str) -> EvalCase:
    return EvalCase(
        id="unit",
        user="Cheapest 4 vCPU 8 GB in EU?",
        tool_call={
            "name": "compare",
            "args": {
                "vcpu": 4,
                "ram_gb": 8,
                "region": "eu-central",
                "family": "general-purpose",
                "providers": ["aws"],
                "expand": "cheapest",
            },
        },
        tool_result={
            "request": {
                "vcpu": 4,
                "ram_gb": 8,
                "region": "eu-central",
                "family": "general-purpose",
                "providers": ["aws"],
            },
            "results": [
                {
                    "provider": "aws",
                    "instance_type": "m5.xlarge",
                    "monthly_usd": 140.16,
                    "citation": {"age_hours": 6.0},
                }
            ],
            "unmet_requirements": [],
        },
        final_answer=final_answer,
        checks=["tool_call", "price_provenance", "snapshot_age", "candidate_coverage"],
        expect={"required_providers": ["aws"], "required_candidates": ["m5.xlarge"]},
    )


def test_eval_case_suites_all_pass():
    cases = load_cases()

    assert len(cases) == 5
    for case in cases:
        failures = [result for result in grade_case(case) if not result.passed]
        assert failures == []


def test_price_not_in_tool_result_fails():
    case = _base_case("AWS m5.xlarge costs $999.99/mo (snapshot 6h old).")

    failures = [result for result in grade_case(case) if not result.passed]

    assert any(result.name == "price_provenance" for result in failures)


def test_price_without_snapshot_age_fails():
    case = _base_case("AWS m5.xlarge costs $140.16/mo.")

    failures = [result for result in grade_case(case) if not result.passed]

    assert any(result.name == "snapshot_age" for result in failures)


def test_missing_data_answer_cannot_quote_prices():
    case = EvalCase(
        id="missing",
        user="Cheapest VM on unsupported provider?",
        tool_call={
            "name": "compare",
            "args": {
                "vcpu": 4,
                "ram_gb": 8,
                "region": "antarctica",
                "family": "any",
                "providers": ["fly"],
                "expand": "cheapest",
            },
        },
        tool_result={
            "request": {
                "vcpu": 4,
                "ram_gb": 8,
                "region": "antarctica",
                "family": "any",
                "providers": ["fly"],
            },
            "results": [],
            "unmet_requirements": [{"provider": "fly"}],
        },
        final_answer="I do not have covered pricing data, but it is probably $10/mo.",
        checks=["missing_data_refusal"],
    )

    failures = [result for result in grade_case(case) if not result.passed]

    assert any(result.name == "missing_data_refusal" for result in failures)
