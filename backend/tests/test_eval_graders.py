from __future__ import annotations

from evals.cases import EvalCase, load_cases
from evals.graders import grade_case
from evals.replay import replay_case


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

    assert {case.id for case in cases} == EXPECTED_CASE_IDS
    assert all(case.rail is not None for case in cases)
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
