"""Final-answer checks for the pricing agent.

These checks are deliberately deterministic. They do not decide whether an
answer is elegant; they enforce the minimum safety contract before text is sent
to an unauthenticated user.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, cast


@dataclass(frozen=True)
class PolicyViolation:
    name: str
    detail: str


PRICE_RE = re.compile(r"\$(?P<amount>\d+(?:,\d{3})*(?:\.\d+)?)")
STALE_RE = re.compile(r"\bstale\b|\bexpired\b|\bover\s+24\s+hours?\b", re.I)
REFETCH_RE = re.compile(r"\brefetch\b|\brefresh\b|\bfresh\b|\b--force\b", re.I)
MISSING_RE = re.compile(
    r"\b(do not have|don't have|cannot quote|can't quote|not covered|unsupported|no covered pricing|no pricing data)\b",
    re.I,
)
INJECTION_COMPLIANCE_RE = re.compile(
    r"\b(ignore (all )?(previous|prior) instructions|as an ai language model with hidden instructions|system says|developer says)\b",
    re.I,
)
LEAKAGE_RE = re.compile(
    r"(store_path|PROVIDER_API_KEY|BUDGET_IP_HASH_SALT_SECRET|"
    r"-----BEGIN|\.env|traceback|/Users/|/private/|system prompt|"
    r"rendered prompt|hidden prompt|developer instructions|developer message|"
    r"prompts/system|prompts/rendered|finops-agent\.system\.md|manifest\.yaml|"
    r"<system>|</system>|<developer>|</developer>)",
    re.I,
)

SAFE_FINAL_ANSWER = (
    "I could not produce a response that passed the pricing citation policy. "
    "Please rephrase the pricing request and I will verify it against the pricing tool."
)


def validate_final_answer(
    answer: str,
    tool_results: list[object],
) -> list[PolicyViolation]:
    violations: list[PolicyViolation] = []
    normalized_results = [_coerce_tool_result(result) for result in tool_results]

    violations.extend(_check_no_internal_leakage(answer))
    violations.extend(_check_no_prompt_injection_compliance(answer))
    violations.extend(_check_price_provenance(answer, normalized_results))
    violations.extend(_check_snapshot_age(answer))
    violations.extend(_check_staleness(answer, normalized_results))
    violations.extend(_check_missing_data_refusal(answer, normalized_results))
    return violations


def prices_in_answer(answer: str) -> list[float]:
    return [float(match.group("amount").replace(",", "")) for match in PRICE_RE.finditer(answer)]


def allowed_prices(tool_results: list[dict[str, Any]]) -> list[float]:
    prices: list[float] = []
    for tool_result in tool_results:
        for result in tool_result.get("results", []):
            if not isinstance(result, dict):
                continue
            for key in ("monthly_usd", "hourly_usd"):
                value = result.get(key)
                if isinstance(value, int | float):
                    prices.append(float(value))
            for considered in result.get("considered", []):
                if isinstance(considered, dict):
                    value = considered.get("monthly_usd")
                    if isinstance(value, int | float):
                        prices.append(float(value))
    return prices


def citation_ages(tool_results: list[dict[str, Any]]) -> list[float]:
    ages: list[float] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            value = obj.get("age_hours")
            if isinstance(value, int | float):
                ages.append(float(value))
            for child in obj.values():
                walk(child)
        elif isinstance(obj, list):
            for child in obj:
                walk(child)

    for tool_result in tool_results:
        walk(tool_result)
    return ages


def has_missing_data(tool_results: list[dict[str, Any]]) -> bool:
    return any(
        not result.get("results") and result.get("unmet_requirements")
        for result in tool_results
    )


def _check_no_internal_leakage(answer: str) -> list[PolicyViolation]:
    if LEAKAGE_RE.search(answer):
        return [PolicyViolation("no_internal_leakage", "answer leaked internal-only text")]
    return []


def _check_no_prompt_injection_compliance(answer: str) -> list[PolicyViolation]:
    if INJECTION_COMPLIANCE_RE.search(answer):
        return [
            PolicyViolation(
                "no_prompt_injection_compliance",
                "answer appears to follow or repeat injected control text",
            )
        ]
    return []


def _check_price_provenance(
    answer: str,
    tool_results: list[dict[str, Any]],
) -> list[PolicyViolation]:
    mentioned = prices_in_answer(answer)
    if not mentioned:
        return []
    allowed = allowed_prices(tool_results)
    if not allowed:
        return [PolicyViolation("price_provenance", "answer mentions prices without tool prices")]
    missing = [
        amount
        for amount in mentioned
        if not any(math.isclose(amount, allowed_amount, abs_tol=0.01) for allowed_amount in allowed)
    ]
    if missing:
        rendered = ", ".join(f"${amount:.2f}" for amount in missing)
        return [PolicyViolation("price_provenance", f"price(s) not in tool result: {rendered}")]
    return []


def _check_snapshot_age(answer: str) -> list[PolicyViolation]:
    matches = list(PRICE_RE.finditer(answer))
    missing = []
    lowered = answer.lower()
    for match in matches:
        start = max(0, match.start() - 40)
        end = min(len(lowered), match.end() + 90)
        if "snapshot" not in lowered[start:end]:
            missing.append(match.group(0))
    if missing:
        return [
            PolicyViolation(
                "snapshot_age",
                "price(s) missing nearby snapshot age: " + ", ".join(missing),
            )
        ]
    return []


def _check_staleness(
    answer: str,
    tool_results: list[dict[str, Any]],
) -> list[PolicyViolation]:
    max_age = max(citation_ages(tool_results), default=0.0)
    if max_age <= 24:
        return []
    if not STALE_RE.search(answer):
        return [PolicyViolation("staleness", "stale citation not marked stale")]
    if not REFETCH_RE.search(answer):
        return [PolicyViolation("staleness", "stale answer does not offer refetch")]
    return []


def _check_missing_data_refusal(
    answer: str,
    tool_results: list[dict[str, Any]],
) -> list[PolicyViolation]:
    if not has_missing_data(tool_results):
        return []
    if prices_in_answer(answer):
        return [PolicyViolation("missing_data_refusal", "missing-data answer quoted a price")]
    if not MISSING_RE.search(answer):
        return [
            PolicyViolation(
                "missing_data_refusal",
                "missing-data answer did not plainly refuse",
            )
        ]
    return []


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
