from __future__ import annotations

import json

from agent.policy.answer_plan import (
    AnswerPlan,
    render_answer_plan,
    render_checked_answer_plan,
    validate_answer_plan,
)


def _tool_result() -> dict:
    return {
        "results": [
            {
                "provider": "aws",
                "instance_type": "m5.xlarge",
                "region_native": "us-east-1",
                "monthly_usd": 138.24,
                "hourly_usd": 0.192,
                "citation": {
                    "source_url": "https://aws.example/prices",
                    "json_path": "$.aws.m5",
                    "fetched_at": "2026-06-05T10:00:00Z",
                    "age_hours": 4.0,
                    "snapshot": {
                        "provider": "aws",
                        "snapshot_iso": "2026-06-05T10-00-00Z",
                        "filename": "us-east-1.json",
                    },
                },
            }
        ],
        "ranked_by": "monthly_usd",
        "unmet_requirements": [],
    }


def _answer_plan(**overrides: object) -> dict:
    plan: dict[str, object] = {
        "answer_type": "ranking",
        "price_claims": [
            {
                "provider": "aws",
                "instance_type": "m5.xlarge",
                "region_native": "us-east-1",
                "monthly_usd": 138.24,
                "hourly_usd": 0.192,
                "snapshot_age_hours": 4.0,
                "source_result_index": 0,
                "citation": {
                    "source_url": "https://aws.example/prices",
                    "json_path": "$.aws.m5",
                    "snapshot": {
                        "provider": "aws",
                        "snapshot_iso": "2026-06-05T10-00-00Z",
                        "filename": "us-east-1.json",
                    },
                },
            }
        ],
        "candidate_claims": [
            {
                "provider": "aws",
                "instance_type": "m5.xlarge",
                "monthly_usd": 138.24,
                "snapshot_age_hours": 4.0,
                "source_result_index": 0,
            }
        ],
    }
    plan.update(overrides)
    return plan


def test_valid_answer_plan_renders_verified_prose():
    plan = AnswerPlan.model_validate(_answer_plan())

    assert validate_answer_plan(plan, [_tool_result()]) == []
    assert render_answer_plan(plan) == (
        "Cheapest is AWS m5.xlarge at $138.24/mo in us-east-1 (snapshot 4h old). "
        "Candidates considered: AWS considered m5.xlarge at $138.24/mo (snapshot 4h old)."
    )


def test_render_checked_answer_plan_accepts_json_fence():
    text = "```json\n" + json.dumps(_answer_plan()) + "\n```"

    rendered, violations = render_checked_answer_plan(text, [_tool_result()])

    assert violations == []
    assert rendered is not None
    assert "$138.24/mo" in rendered


def test_fabricated_price_claim_fails_binding():
    data = _answer_plan()
    data["price_claims"][0]["monthly_usd"] = 999.99  # type: ignore[index]
    plan = AnswerPlan.model_validate(data)

    violations = validate_answer_plan(plan, [_tool_result()])

    assert any(violation.name == "answer_plan_price_binding" for violation in violations)


def test_candidate_price_requires_verified_snapshot_age():
    data = _answer_plan()
    del data["candidate_claims"][0]["snapshot_age_hours"]  # type: ignore[index]
    plan = AnswerPlan.model_validate(data)

    violations = validate_answer_plan(plan, [_tool_result()])

    assert any(violation.name == "answer_plan_candidate" for violation in violations)


def test_stale_claim_requires_stale_answer_type():
    data = _answer_plan()
    data["price_claims"][0]["snapshot_age_hours"] = 52.0  # type: ignore[index]
    data["candidate_claims"][0]["snapshot_age_hours"] = 52.0  # type: ignore[index]
    plan = AnswerPlan.model_validate(data)

    violations = validate_answer_plan(plan, [_tool_result()])

    assert any(violation.name == "answer_plan_staleness" for violation in violations)


def test_missing_data_unmet_claim_must_match_tool_result():
    plan = AnswerPlan.model_validate(
        {
            "answer_type": "missing_data",
            "unmet_requirements": [{"provider": "gcp", "reason": "provider_not_supported"}],
        }
    )
    tool_result = {"results": [], "unmet_requirements": [{"provider": "fly", "reason": "provider_not_supported"}]}

    violations = validate_answer_plan(plan, [tool_result])

    assert any(violation.name == "answer_plan_unmet_binding" for violation in violations)
