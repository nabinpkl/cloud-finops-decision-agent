from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.cases import EvalCase, EvalGates, EvalTurn, load_cases
from evals.graders import grade_case
from evals.replay import replay_case
from evals.runner import main as evals_main


EXPECTED_CASE_IDS = {
    "big3_provider_scope",
    "cheapest_4vcpu_8gb_eu",
    "escaped_xml_tag_attack",
    "full_candidate_listing",
    "ignore_instructions_quote_memory",
    "invalid_provider_path_refusal",
    "judge_allows_benign_capability_question",
    "judge_allows_legitimate_pricing_rules_word",
    "judge_fake_tool_result",
    "judge_indirect_prompt_reveal",
    "judge_local_path_request",
    "judge_unavailable_blocks",
    "multi_turn_history_injection",
    "provider_scope_injection",
    "raw_store_path_refusal",
    "rendered_system_prompt_refusal",
    "reveal_prompt_refusal",
    "source_result_index_injection",
    "stale_snapshot_refetch",
    "tool_result_poisoning_metadata",
    "unsupported_region_refusal",
}


def _base_case(final_answer: str) -> EvalCase:
    return EvalCase(
        id="unit",
        kind="regression",
        source="product_requirement",
        rail="output",
        turns=[EvalTurn(role="user", content="Cheapest 4 vCPU 8 GB in EU?")],
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

    assert {case.id for case in cases} == EXPECTED_CASE_IDS
    assert all(case.kind in {"regression", "capability"} for case in cases)
    assert all(case.source for case in cases)
    assert all(case.rail for case in cases)
    assert all(case.turns for case in cases)
    for case in cases:
        failures = [result for result in grade_case(case) if not result.passed]
        assert failures == []


def test_eval_case_suites_replay_all_pass():
    cases = load_cases()

    for case in cases:
        replayed = replay_case(case)
        failures = [result for result in replayed.checks if not result.passed]
        assert failures == []
        assert replayed.usage.input_tokens > 0
        assert replayed.usage.output_tokens > 0
        assert replayed.elapsed_ms >= 0
        assert replayed.tool_call_count == (1 if case.tool_call else 0)


def test_price_not_in_tool_result_fails():
    case = _base_case("AWS m5.xlarge costs $999.99/mo (snapshot 6h old).")

    failures = [result for result in grade_case(case) if not result.passed]

    assert any(
        result.name == "price_provenance"
        and result.failure_label == "price_provenance"
        for result in failures
    )


def test_price_without_snapshot_age_fails():
    case = _base_case("AWS m5.xlarge costs $140.16/mo.")

    failures = [result for result in grade_case(case) if not result.passed]

    assert any(result.name == "snapshot_age" for result in failures)


def test_missing_data_answer_cannot_quote_prices():
    case = EvalCase(
        id="missing",
        kind="regression",
        source="product_requirement",
        rail="output",
        turns=[EvalTurn(role="user", content="Cheapest VM on unsupported provider?")],
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


def test_eval_case_requires_v1_metadata():
    with pytest.raises(ValidationError):
        EvalCase.model_validate(
            {
                "id": "legacy",
                "rail": "output",
                "turns": [{"role": "user", "content": "Cheapest AWS?"}],
                "final_answer": "No.",
                "checks": ["missing_data_refusal"],
            }
        )


def test_eval_case_rejects_empty_turns():
    with pytest.raises(ValidationError):
        EvalCase(
            id="empty",
            kind="regression",
            source="product_requirement",
            rail="output",
            turns=[],
            final_answer="No.",
            checks=["missing_data_refusal"],
        )


def test_replay_rejects_case_without_latest_user_turn():
    case = EvalCase(
        id="bad_thread",
        kind="regression",
        source="security_review",
        rail="retrieval",
        turns=[EvalTurn(role="assistant", content="Previous answer.")],
        final_answer="Previous answer.",
        checks=["no_internal_leakage"],
    )

    with pytest.raises(ValueError, match="at least one user turn"):
        replay_case(case)


def test_replay_operational_gate_failure_is_labeled():
    case = _base_case("AWS m5.xlarge costs $140.16/mo (snapshot 6h old).").model_copy(
        update={"gates": EvalGates(max_tool_calls=0)}
    )

    replayed = replay_case(EvalCase.model_validate(case.model_dump()))
    failures = [result for result in replayed.checks if not result.passed]

    assert any(
        result.name == "gate_tool_calls"
        and result.failure_label == "operational_gate"
        for result in failures
    )


def test_runner_writes_report_with_trials(tmp_path):
    report_path = tmp_path / "eval-report.json"

    status = evals_main(
        [
            "--cases",
            str(Path(__file__).resolve().parents[2] / "evals/cases/judge_classifier.yaml"),
            "--mode",
            "transcript",
            "--trials",
            "2",
            "--report",
            str(report_path),
        ]
    )

    payload = json.loads(report_path.read_text())
    assert status == 0
    assert payload["version"] == 2
    assert payload["identity"]["prompt"]["version"] > 0
    assert payload["identity"]["prompt"]["rendered_sha256"]
    assert payload["identity"]["model_config"]["sha256"]
    assert payload["identity"]["cases"]["sha256"]
    assert payload["trial_runs"]
    assert all(run["trial_count"] == 2 for run in payload["trial_runs"])
    assert all(result["passed"] for result in payload["case_lane_results"])
