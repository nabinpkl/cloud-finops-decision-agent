"""Deterministic validation for model-emitted pricing answer plans."""

from __future__ import annotations

import math
from typing import Any, cast

from agent.policy.answer_plan_models import (
    AnswerPlan,
    CandidateClaim,
    CompositeCitation,
    PriceClaim,
    SourceCitation,
    UnmetRequirementClaim,
)
from agent.policy.final_answer import PolicyViolation
from agent.tools.view_models import ViewSpec
from normalize.taxonomy.columns import get_column, is_refused


def validate_answer_plan(
    plan: AnswerPlan,
    tool_results: list[dict[str, Any]],
) -> list[PolicyViolation]:
    violations: list[PolicyViolation] = []
    latest_tool_result = _latest_pricing_tool_result(tool_results)
    results = _result_rows(latest_tool_result)
    unmet = _unmet_rows(latest_tool_result)

    if plan.answer_type in {"missing_data", "refusal"} and plan.price_claims:
        violations.append(
            PolicyViolation(
                "answer_plan_price_for_refusal",
                f"{plan.answer_type} answer must not include price claims",
            )
        )
    if plan.answer_type in {"ranking", "lookup", "stale"} and not plan.price_claims:
        violations.append(
            PolicyViolation(
                "answer_plan_missing_price",
                f"{plan.answer_type} answer requires at least one price claim",
            )
        )
    if plan.answer_type == "lookup" and len(plan.price_claims) > 1:
        violations.append(
            PolicyViolation("answer_plan_lookup_shape", "lookup answer must include exactly one price claim")
        )
    if plan.answer_type == "missing_data" and not plan.unmet_requirements:
        violations.append(
            PolicyViolation(
                "answer_plan_missing_unmet",
                "missing_data answer requires at least one unmet requirement",
            )
        )
    if plan.answer_type == "stale" and plan.price_claims:
        if max(claim.snapshot_age_hours for claim in plan.price_claims) <= 24:
            violations.append(
                PolicyViolation("answer_plan_staleness", "stale answer has no stale claim")
            )
    if plan.answer_type != "stale":
        stale_claims = [claim for claim in plan.price_claims if claim.snapshot_age_hours > 24]
        if stale_claims:
            violations.append(
                PolicyViolation("answer_plan_staleness", "stale claim requires stale answer_type")
            )

    for claim in plan.price_claims:
        violations.extend(_validate_price_claim(claim, results))
    for claim in plan.candidate_claims:
        violations.extend(_validate_candidate_claim(claim, results))
    for claim in plan.unmet_requirements:
        violations.extend(_validate_unmet_claim(claim, unmet))

    if plan.price_claims and _claim_indexes(plan.price_claims) != sorted(_claim_indexes(plan.price_claims)):
        violations.append(
            PolicyViolation("answer_plan_ranking", "price claims must preserve result order")
        )

    if plan.view_spec is not None:
        violations.extend(_validate_view_spec(plan.view_spec, results))
    return violations


def _validate_view_spec(
    view: ViewSpec,
    results: list[dict[str, Any]],
) -> list[PolicyViolation]:
    """Enforce the generative-view contract on a ViewSpec (TASKS R6/R7).

    Thin adapter over :func:`validate_view_spec_fields`, the single source of
    truth shared with the ``set_view`` tool-result path that mutates the
    backend-authoritative view-state. Folding both representations through one
    validator means the spec that lands in ``state['view']`` is validated by the
    same rules that gate the rendered prose.
    """
    return validate_view_spec_fields(
        columns=[col.column_id for col in view.columns],
        group_by=view.group_by,
        sort_column=view.sort.column_id if view.sort is not None else None,
        source_result_indices=view.source_result_indices,
        refused_columns=view.refused_columns,
        results=results,
    )


def validate_view_spec_fields(
    *,
    columns: list[str],
    group_by: str | None,
    sort_column: str | None,
    source_result_indices: list[int],
    refused_columns: list[str],
    results: list[dict[str, Any]],
) -> list[PolicyViolation]:
    """Registry + Tier-3-refusal + row-binding validation for any view spec.

    Every chosen column must resolve to a registered Tier-1 field or Tier-2
    formula; a Tier-3 (refused) or unregistered column rejects the whole spec.
    Every shown row index must bind to a validated tool-result row. Tier-3
    columns the user asked for belong in ``refused_columns`` (graceful refusal,
    never rendered). This is the one validator both the AnswerPlan view-spec
    (gates rendered prose) and the ``set_view`` tool result (gates the
    backend-authoritative ``state['view']`` mutation) call, so "backend writes
    state ONLY from validated results" holds on the path that actually mutates
    state.
    """
    violations: list[PolicyViolation] = []

    for column_id in columns:
        entry = get_column(column_id)
        if entry is None:
            violations.append(
                PolicyViolation(
                    "view_spec_unregistered_column",
                    f"column '{column_id}' is not in the column registry",
                )
            )
        elif entry.tier == 3:
            violations.append(
                PolicyViolation(
                    "view_spec_refused_column",
                    f"column '{column_id}' is not normalized and cannot be shown; "
                    "list it under refused_columns instead",
                )
            )

    if group_by is not None and get_column(group_by) is None:
        violations.append(
            PolicyViolation(
                "view_spec_unregistered_column",
                f"group_by column '{group_by}' is not in the column registry",
            )
        )
    if sort_column is not None and get_column(sort_column) is None:
        violations.append(
            PolicyViolation(
                "view_spec_unregistered_column",
                f"sort column '{sort_column}' is not in the column registry",
            )
        )

    # Every shown row binds to a real validated result row. A negative index
    # would silently wrap with Python slicing semantics, so reject it explicitly.
    for idx in source_result_indices:
        if idx < 0 or _result_at(results, idx) is None:
            violations.append(
                PolicyViolation(
                    "view_spec_row_binding",
                    f"view row index {idx} does not bind to a validated result",
                )
            )

    # refused_columns must actually be Tier-3 (graceful refusal, not a dumping
    # ground for typos or cited columns).
    for cid in refused_columns:
        if not is_refused(cid):
            violations.append(
                PolicyViolation(
                    "view_spec_refusal_misuse",
                    f"refused_columns entry '{cid}' is not a refused (Tier-3) column",
                )
            )
    return violations


def _validate_price_claim(
    claim: PriceClaim,
    results: list[dict[str, Any]],
) -> list[PolicyViolation]:
    row = _result_at(results, claim.source_result_index)
    if row is None:
        return [PolicyViolation("answer_plan_source_result", "price claim references missing result")]
    violations: list[PolicyViolation] = []
    for key in ("provider", "instance_type", "region_native"):
        if claim.model_dump()[key] != row.get(key):
            violations.append(
                PolicyViolation(
                    "answer_plan_claim_binding",
                    f"{key} does not match source result",
                )
            )
    for key in ("monthly_usd", "hourly_usd"):
        claim_value = getattr(claim, key)
        row_value = row.get(key)
        if claim_value is not None and not _same_number(claim_value, row_value):
            violations.append(
                PolicyViolation(
                    "answer_plan_price_binding",
                    f"{key} does not match source result",
                )
            )
    row_age = _citation_age(row.get("citation"))
    if row_age is None or not _same_number(claim.snapshot_age_hours, row_age):
        violations.append(
            PolicyViolation("answer_plan_age_binding", "snapshot age does not match source result")
        )
    if not _citation_matches(claim.citation, row.get("citation")):
        violations.append(
            PolicyViolation("answer_plan_citation_binding", "citation does not match source result")
        )
    return violations


def _validate_candidate_claim(
    claim: CandidateClaim,
    results: list[dict[str, Any]],
) -> list[PolicyViolation]:
    row = _result_at(results, claim.source_result_index)
    if row is None:
        return [PolicyViolation("answer_plan_candidate", "candidate references missing result")]
    source = row
    if claim.considered_index is not None:
        considered = row.get("considered", [])
        if not isinstance(considered, list) or claim.considered_index >= len(considered):
            return [PolicyViolation("answer_plan_candidate", "candidate references missing considered row")]
        source = considered[claim.considered_index]
    violations: list[PolicyViolation] = []
    if claim.provider != row.get("provider"):
        violations.append(PolicyViolation("answer_plan_candidate", "candidate provider mismatch"))
    if claim.instance_type != source.get("instance_type"):
        violations.append(PolicyViolation("answer_plan_candidate", "candidate instance mismatch"))
    if claim.monthly_usd is not None and not _same_number(claim.monthly_usd, source.get("monthly_usd")):
        violations.append(PolicyViolation("answer_plan_candidate", "candidate price mismatch"))
    if claim.monthly_usd is not None:
        row_age = _citation_age(row.get("citation"))
        if claim.snapshot_age_hours is None or row_age is None:
            violations.append(PolicyViolation("answer_plan_candidate", "candidate price missing snapshot age"))
        elif not _same_number(claim.snapshot_age_hours, row_age):
            violations.append(PolicyViolation("answer_plan_candidate", "candidate snapshot age mismatch"))
    return violations


def _validate_unmet_claim(
    claim: UnmetRequirementClaim,
    unmet: list[dict[str, Any]],
) -> list[PolicyViolation]:
    for row in unmet:
        if claim.provider is not None and claim.provider != row.get("provider"):
            continue
        if claim.region is not None and claim.region != row.get("region"):
            continue
        if claim.reason != row.get("reason"):
            continue
        return []
    return [PolicyViolation("answer_plan_unmet_binding", "unmet requirement does not match tool result")]


def _citation_matches(claim_citation: SourceCitation | CompositeCitation, row_citation: Any) -> bool:
    if not isinstance(row_citation, dict):
        return False
    if isinstance(claim_citation, SourceCitation):
        return _source_citation_matches(claim_citation, row_citation)
    row_composite = row_citation.get("composite")
    if not isinstance(row_composite, list) or len(row_composite) != len(claim_citation.composite):
        return False
    for claim_item, row_item in zip(claim_citation.composite, row_composite, strict=True):
        if not isinstance(row_item, dict):
            return False
        if not _source_citation_matches(claim_item, cast(dict[str, Any], row_item)):
            return False
    return True


def _source_citation_matches(claim_citation: SourceCitation, row_citation: dict[str, Any]) -> bool:
    return (
        claim_citation.source_url == row_citation.get("source_url")
        and claim_citation.json_path == row_citation.get("json_path")
        and claim_citation.snapshot.model_dump() == row_citation.get("snapshot")
    )


def _citation_age(citation: Any) -> float | None:
    if not isinstance(citation, dict):
        return None
    age = citation.get("age_hours")
    if isinstance(age, int | float):
        return float(age)
    composite = citation.get("composite")
    if isinstance(composite, list):
        ages = [
            float(entry["age_hours"])
            for entry in composite
            if isinstance(entry, dict) and isinstance(entry.get("age_hours"), int | float)
        ]
        if ages:
            return max(ages)
    return None


def _latest_pricing_tool_result(tool_results: list[dict[str, Any]]) -> dict[str, Any] | None:
    for result in reversed(tool_results):
        if not isinstance(result, dict):
            continue
        if any(key in result for key in ("results", "result", "unmet_requirements")):
            return result
    return None


def _result_rows(tool_result: dict[str, Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if tool_result is None:
        return out
    rows = tool_result.get("results")
    if isinstance(rows, list):
        out.extend(row for row in rows if isinstance(row, dict))
    single = tool_result.get("result")
    if isinstance(single, dict):
        out.append(single)
    return out


def _unmet_rows(tool_result: dict[str, Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if tool_result is None:
        return out
    rows = tool_result.get("unmet_requirements")
    if isinstance(rows, list):
        out.extend(row for row in rows if isinstance(row, dict))
    return out


def _result_at(results: list[dict[str, Any]], index: int) -> dict[str, Any] | None:
    if index >= len(results):
        return None
    return results[index]


def _claim_indexes(claims: list[PriceClaim]) -> list[int]:
    return [claim.source_result_index for claim in claims]


def _same_number(left: object, right: object) -> bool:
    if not isinstance(left, int | float) or not isinstance(right, int | float):
        return False
    return math.isclose(float(left), float(right), abs_tol=0.01)
