"""Column registry + view-spec validation (TASKS R5/R6/R7).

The agent decides the view but cannot invent it: every chosen column must
resolve to a registered Tier-1 source field or Tier-2 derived formula, every
shown row must bind to a validated tool-result row, and Tier-3 dimensions the
snapshot cannot back are refused (graceful), never fabricated. The view-spec is
folded into the AnswerPlan so one validator covers both.
"""

from __future__ import annotations

from agent.policy.answer_plan import AnswerPlan, validate_answer_plan
from normalize.taxonomy import columns as registry


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


def _base_plan() -> dict:
    return {
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
    }


def _plan(view_spec: dict) -> AnswerPlan:
    data = _base_plan()
    data["view_spec"] = view_spec
    return AnswerPlan.model_validate(data)


# --- registry loader -------------------------------------------------------


def test_registry_tiers_resolve():
    for cid, tier in (
        ("provider", 1),
        ("dollar_per_vcpu", 2),
        ("network_bandwidth", 3),
    ):
        entry = registry.get_column(cid)
        assert entry is not None
        assert entry.tier == tier


def test_tier2_carries_formula_and_cited_inputs():
    entry = registry.get_column("dollar_per_vcpu")
    assert entry is not None
    assert entry.formula == "monthly_usd / vcpu_actual"
    assert "monthly_usd" in entry.cited_inputs
    assert "vcpu_actual" in entry.cited_inputs


def test_tier3_is_refused():
    assert registry.is_refused("cpu_generation") is True
    assert registry.is_refused("provider") is False


def test_cited_columns_excludes_tier3():
    cited = registry.cited_columns()
    assert "monthly_usd" in cited
    assert "dollar_per_vcpu" in cited
    assert "network_bandwidth" not in cited


# --- view-spec validation --------------------------------------------------


def test_registered_columns_pass():
    plan = _plan(
        {
            "columns": [{"column_id": "provider"}, {"column_id": "monthly_usd"},
                        {"column_id": "dollar_per_vcpu"}],
            "source_result_indices": [0],
        }
    )
    assert validate_answer_plan(plan, [_tool_result()]) == []


def test_unregistered_column_rejects_plan():
    plan = _plan({"columns": [{"column_id": "made_up_metric"}]})
    names = {v.name for v in validate_answer_plan(plan, [_tool_result()])}
    assert "view_spec_unregistered_column" in names


def test_tier3_column_in_columns_rejects():
    plan = _plan({"columns": [{"column_id": "network_bandwidth"}]})
    names = {v.name for v in validate_answer_plan(plan, [_tool_result()])}
    assert "view_spec_refused_column" in names


def test_tier3_in_refused_columns_is_graceful():
    plan = _plan(
        {
            "columns": [{"column_id": "provider"}],
            "refused_columns": ["network_bandwidth"],
        }
    )
    assert validate_answer_plan(plan, [_tool_result()]) == []


def test_refused_columns_must_be_tier3():
    plan = _plan(
        {
            "columns": [{"column_id": "provider"}],
            "refused_columns": ["monthly_usd"],
        }
    )
    names = {v.name for v in validate_answer_plan(plan, [_tool_result()])}
    assert "view_spec_refusal_misuse" in names


def test_row_index_must_bind_to_result():
    plan = _plan(
        {
            "columns": [{"column_id": "provider"}],
            "source_result_indices": [0, 5],
        }
    )
    names = {v.name for v in validate_answer_plan(plan, [_tool_result()])}
    assert "view_spec_row_binding" in names


def test_group_by_and_sort_columns_validated():
    plan = _plan(
        {
            "columns": [{"column_id": "provider"}],
            "group_by": "not_a_column",
            "sort": {"column_id": "also_not", "direction": "asc"},
        }
    )
    names = {v.name for v in validate_answer_plan(plan, [_tool_result()])}
    assert "view_spec_unregistered_column" in names


def test_plan_without_view_spec_still_validates():
    # View-spec is optional; existing claim-only plans are unaffected.
    plan = AnswerPlan.model_validate(_base_plan())
    assert validate_answer_plan(plan, [_tool_result()]) == []
