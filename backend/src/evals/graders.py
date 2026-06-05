from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Callable

from evals.cases import EvalCase


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


PRICE_RE = re.compile(r"\$(?P<amount>\d+(?:,\d{3})*(?:\.\d+)?)")
STALE_RE = re.compile(r"\bstale\b|\bexpired\b|\bover\s+24\s+hours?\b", re.I)
REFETCH_RE = re.compile(r"\brefetch\b|\brefresh\b|\bfresh\b|\b--force\b", re.I)
MISSING_RE = re.compile(
    r"\b(do not have|don't have|cannot quote|can't quote|not covered|unsupported|no covered pricing|no pricing data)\b",
    re.I,
)


def grade_case(case: EvalCase) -> list[CheckResult]:
    checks: dict[str, Callable[[EvalCase], CheckResult]] = {
        "tool_call": check_tool_call,
        "price_provenance": check_price_provenance,
        "snapshot_age": check_snapshot_age,
        "staleness": check_staleness,
        "missing_data_refusal": check_missing_data_refusal,
        "candidate_coverage": check_candidate_coverage,
        "provider_scope": check_provider_scope,
    }
    results: list[CheckResult] = []
    for check_name in case.checks:
        check = checks.get(check_name)
        if check is None:
            results.append(CheckResult(check_name, False, "unknown check"))
            continue
        results.append(check(case))
    return results


def check_tool_call(case: EvalCase) -> CheckResult:
    call = case.tool_call
    if call.get("name") != "compare":
        return CheckResult("tool_call", False, "tool call name must be compare")
    args = call.get("args")
    if not isinstance(args, dict):
        return CheckResult("tool_call", False, "tool call args must be an object")
    expected = _expected_request(case)
    for key, expected_value in expected.items():
        if args.get(key) != expected_value:
            return CheckResult(
                "tool_call",
                False,
                f"arg {key!r} expected {expected_value!r}, got {args.get(key)!r}",
            )
    return CheckResult("tool_call", True, "tool call matches request contract")


def check_price_provenance(case: EvalCase) -> CheckResult:
    mentioned = _prices_in_answer(case.final_answer)
    if not mentioned:
        return CheckResult("price_provenance", False, "answer mentions no prices")
    allowed = _allowed_prices(case.tool_result)
    if not allowed:
        return CheckResult("price_provenance", False, "tool result has no prices")
    missing = [
        amount
        for amount in mentioned
        if not any(math.isclose(amount, allowed_amount, abs_tol=0.01) for allowed_amount in allowed)
    ]
    if missing:
        rendered = ", ".join(f"${amount:.2f}" for amount in missing)
        return CheckResult("price_provenance", False, f"price(s) not in tool result: {rendered}")
    return CheckResult("price_provenance", True, "all mentioned prices came from tool result")


def check_snapshot_age(case: EvalCase) -> CheckResult:
    matches = list(PRICE_RE.finditer(case.final_answer))
    if not matches:
        return CheckResult("snapshot_age", False, "answer mentions no prices")
    missing = []
    answer = case.final_answer.lower()
    for match in matches:
        start = max(0, match.start() - 40)
        end = min(len(answer), match.end() + 90)
        if "snapshot" not in answer[start:end]:
            missing.append(match.group(0))
    if missing:
        return CheckResult(
            "snapshot_age",
            False,
            "price(s) missing nearby snapshot age: " + ", ".join(missing),
        )
    return CheckResult("snapshot_age", True, "each price has nearby snapshot age")


def check_staleness(case: EvalCase) -> CheckResult:
    max_age = max(_citation_ages(case.tool_result), default=0.0)
    if max_age <= 24:
        return CheckResult("staleness", True, "no stale citation expected")
    answer = case.final_answer
    if not STALE_RE.search(answer):
        return CheckResult("staleness", False, "stale citation not marked stale")
    if not REFETCH_RE.search(answer):
        return CheckResult("staleness", False, "stale answer does not offer refetch")
    return CheckResult("staleness", True, "stale citation marked with refetch offer")


def check_missing_data_refusal(case: EvalCase) -> CheckResult:
    if _prices_in_answer(case.final_answer):
        return CheckResult("missing_data_refusal", False, "missing-data answer quoted a price")
    if not MISSING_RE.search(case.final_answer):
        return CheckResult("missing_data_refusal", False, "missing-data answer did not plainly refuse")
    return CheckResult("missing_data_refusal", True, "missing-data answer refused without prices")


def check_candidate_coverage(case: EvalCase) -> CheckResult:
    answer = case.final_answer.lower()
    required = [str(item) for item in case.expect.get("required_candidates", [])]
    missing = [item for item in required if item.lower() not in answer]
    if missing:
        return CheckResult("candidate_coverage", False, "missing candidate(s): " + ", ".join(missing))
    required_providers = [str(item) for item in case.expect.get("required_providers", [])]
    missing_providers = [item for item in required_providers if item.lower() not in answer]
    if missing_providers:
        return CheckResult(
            "candidate_coverage",
            False,
            "missing provider(s): " + ", ".join(missing_providers),
        )
    return CheckResult("candidate_coverage", True, "required candidates/providers are present")


def check_provider_scope(case: EvalCase) -> CheckResult:
    args = case.tool_call.get("args", {})
    providers = args.get("providers")
    required = case.expect.get("required_providers", [])
    if providers != required:
        return CheckResult("provider_scope", False, f"providers expected {required!r}, got {providers!r}")
    answer = case.final_answer.lower()
    forbidden = [str(item) for item in case.expect.get("forbidden_providers", [])]
    present = [item for item in forbidden if item.lower() in answer]
    if present:
        return CheckResult("provider_scope", False, "forbidden provider(s) mentioned: " + ", ".join(present))
    return CheckResult("provider_scope", True, "provider scope is respected")


def _expected_request(case: EvalCase) -> dict[str, Any]:
    request = case.tool_result.get("request")
    if not isinstance(request, dict):
        return {}
    expected = {
        key: request[key]
        for key in ("vcpu", "ram_gb", "region", "family", "providers")
        if key in request
    }
    expand = case.tool_call.get("args", {}).get("expand")
    if expand is not None:
        expected["expand"] = expand
    return expected


def _prices_in_answer(answer: str) -> list[float]:
    return [float(match.group("amount").replace(",", "")) for match in PRICE_RE.finditer(answer)]


def _allowed_prices(tool_result: dict[str, Any]) -> list[float]:
    prices: list[float] = []
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


def _citation_ages(tool_result: dict[str, Any]) -> list[float]:
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

    walk(tool_result)
    return ages
