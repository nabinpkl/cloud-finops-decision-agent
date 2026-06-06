"""Structured claim plans for citation-backed pricing answers.

The model emits an AnswerPlan JSON object. This module validates every claim
against the structured compare tool result, then renders user-facing prose from
verified fields only.
"""

from __future__ import annotations

import json
import math
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.policy.final_answer import PolicyViolation


class AnswerPlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SnapshotRef(AnswerPlanModel):
    provider: str
    snapshot_iso: str
    filename: str


class SourceCitation(AnswerPlanModel):
    source_url: str
    json_path: str
    snapshot: SnapshotRef


class CompositeCitation(AnswerPlanModel):
    composite: list[SourceCitation] = Field(min_length=1)


class PriceClaim(AnswerPlanModel):
    provider: str
    instance_type: str
    region_native: str
    monthly_usd: float | None = None
    hourly_usd: float | None = None
    snapshot_age_hours: float
    citation: SourceCitation | CompositeCitation
    source_result_index: int = Field(ge=0)


class CandidateClaim(AnswerPlanModel):
    provider: str
    instance_type: str
    monthly_usd: float | None = None
    snapshot_age_hours: float | None = None
    source_result_index: int = Field(ge=0)
    considered_index: int | None = Field(default=None, ge=0)


class UnmetRequirementClaim(AnswerPlanModel):
    provider: str | None = None
    region: str | None = None
    reason: str


class AnswerPlan(AnswerPlanModel):
    answer_type: Literal["ranking", "lookup", "missing_data", "stale", "refusal"]
    price_claims: list[PriceClaim] = Field(default_factory=list)
    candidate_claims: list[CandidateClaim] = Field(default_factory=list)
    unmet_requirements: list[UnmetRequirementClaim] = Field(default_factory=list)
    refusal_reason: str | None = None


def parse_answer_plan(text: str) -> tuple[AnswerPlan | None, list[PolicyViolation]]:
    try:
        raw = json.loads(_strip_json_markdown(text))
    except json.JSONDecodeError as exc:
        return None, [PolicyViolation("answer_plan_parse", f"invalid JSON: {exc.msg}")]
    try:
        return AnswerPlan.model_validate(raw), []
    except ValidationError as exc:
        return None, [PolicyViolation("answer_plan_schema", str(exc).splitlines()[0])]


def render_checked_answer_plan(
    text: str,
    tool_results: list[object],
) -> tuple[str | None, list[PolicyViolation]]:
    plan, violations = parse_answer_plan(text)
    if violations or plan is None:
        return None, violations
    normalized_results = [_coerce_tool_result(result) for result in tool_results]
    violations = validate_answer_plan(plan, normalized_results)
    if violations:
        return None, violations
    return render_answer_plan(plan), []


def validate_answer_plan(
    plan: AnswerPlan,
    tool_results: list[dict[str, Any]],
) -> list[PolicyViolation]:
    violations: list[PolicyViolation] = []
    results = _all_results(tool_results)
    unmet = _all_unmet(tool_results)

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
    return violations


def render_answer_plan(plan: AnswerPlan) -> str:
    if plan.answer_type == "refusal":
        return _render_refusal(plan.refusal_reason)
    if plan.answer_type == "missing_data":
        return _render_missing_data(plan)
    if plan.answer_type in {"ranking", "lookup", "stale"}:
        return _render_priced_answer(plan)
    return "I cannot answer that request with verified pricing data."


def _render_priced_answer(plan: AnswerPlan) -> str:
    claims = plan.price_claims
    if not claims:
        return _render_missing_data(plan)
    first = claims[0]
    opening = (
        f"Cheapest is {_provider_label(first.provider)} {first.instance_type} "
        f"at {_money(first.monthly_usd)}/mo in {first.region_native} "
        f"(snapshot {_age(first.snapshot_age_hours)}h old)."
    )
    candidates = plan.candidate_claims or [
        CandidateClaim(
            provider=claim.provider,
            instance_type=claim.instance_type,
            monthly_usd=claim.monthly_usd,
            snapshot_age_hours=claim.snapshot_age_hours,
            source_result_index=claim.source_result_index,
        )
        for claim in claims
    ]
    if candidates:
        opening += " Candidates considered: " + _render_candidates(candidates) + "."
    if plan.answer_type == "stale" or any(claim.snapshot_age_hours > 24 for claim in claims):
        opening += " This snapshot is stale, so refetch before relying on it for a buying decision."
    return opening


def _render_candidates(candidates: list[CandidateClaim]) -> str:
    grouped: dict[str, list[CandidateClaim]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.provider, []).append(candidate)
    parts: list[str] = []
    for provider, items in grouped.items():
        rendered_items = []
        for item in items:
            if item.monthly_usd is None:
                rendered_items.append(item.instance_type)
            else:
                age = (
                    ""
                    if item.snapshot_age_hours is None
                    else f" (snapshot {_age(item.snapshot_age_hours)}h old)"
                )
                rendered_items.append(f"{item.instance_type} at {_money(item.monthly_usd)}/mo{age}")
        parts.append(f"{_provider_label(provider)} considered " + _join(rendered_items))
    if len(parts) == 2:
        return "; ".join(parts)
    return _join(parts)


def _render_missing_data(plan: AnswerPlan) -> str:
    if plan.unmet_requirements:
        targets = []
        for item in plan.unmet_requirements:
            if item.provider:
                targets.append(item.provider)
            elif item.region:
                targets.append(item.region)
        if targets:
            return (
                "I do not have covered pricing data for "
                + _join(targets)
                + ", so I cannot quote a verified price for that request."
            )
    return "I do not have covered pricing data for that request, so I cannot quote a verified price."


def _render_refusal(reason: str | None) -> str:
    if reason in {"internal", "prompt", "config", "secret"}:
        return "I cannot reveal internal instructions, configuration, secrets, or local files."
    if reason in {"path", "local_path"}:
        return (
            "I cannot reveal internal local file paths. I can answer pricing questions "
            "with public citation metadata from the pricing tool."
        )
    return "I cannot answer that request outside the pricing citation contract."


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


def _all_results(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for result in tool_results:
        rows = result.get("results")
        if isinstance(rows, list):
            out.extend(row for row in rows if isinstance(row, dict))
        single = result.get("result")
        if isinstance(single, dict):
            out.append(single)
    return out


def _all_unmet(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for result in tool_results:
        rows = result.get("unmet_requirements")
        if isinstance(rows, list):
            out.extend(row for row in rows if isinstance(row, dict))
    return out


def _result_at(results: list[dict[str, Any]], index: int) -> dict[str, Any] | None:
    if index >= len(results):
        return None
    return results[index]


def _claim_indexes(claims: list[PriceClaim]) -> list[int]:
    return [claim.source_result_index for claim in claims]


def _coerce_tool_result(result: object) -> dict[str, Any]:
    if isinstance(result, dict):
        return cast(dict[str, Any], result)
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _strip_json_markdown(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def _provider_label(provider: str) -> str:
    labels = {
        "aws": "AWS",
        "gcp": "GCP",
        "azure": "Azure",
        "oracle": "Oracle",
        "vultr": "Vultr",
        "linode": "Linode",
        "ibm": "IBM",
    }
    return labels.get(provider, provider)


def _money(value: float | None) -> str:
    return "unknown" if value is None else f"${value:,.2f}"


def _age(value: float) -> str:
    return f"{value:g}"


def _join(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _same_number(left: object, right: object) -> bool:
    if not isinstance(left, int | float) or not isinstance(right, int | float):
        return False
    return math.isclose(float(left), float(right), abs_tol=0.01)
