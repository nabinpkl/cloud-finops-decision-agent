"""Deterministic prose rendering for verified pricing answer plans."""

from __future__ import annotations

from agent.policy.answer_plan_models import AnswerPlan, CandidateClaim


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
