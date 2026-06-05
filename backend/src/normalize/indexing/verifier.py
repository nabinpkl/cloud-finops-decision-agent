"""Citation round-trip verifier per ADR 0003.

For a sample of parquet rows, re-loads the raw JSON store_path, resolves the
json_path, and asserts the value equals the price recorded. Returns a
CitationVerification with the sample size, pass/fail counts, and any failures.

The verifier is the build-time integrity gate on the citation contract: a
failure here means the parquet would emit a citation a reader could not
reproduce, which violates the contract."""

from __future__ import annotations

import random
from typing import Any

import orjson
from jsonpath_ng.ext import parse as jsonpath_parse

from ingest._shared import PROJECT_ROOT
from normalize.config import normalize_settings
from normalize.schema import CitationVerification, IndexRow

# Sampling: every snapshot's index is verified on up to N rows, deterministic
# per (provider, snapshot_iso) so a re-run reproduces the same sample.
SAMPLE_CAP = normalize_settings.citation_verifier_sample_cap

# Numeric tolerance when comparing the resolved JSON value to the recorded
# price. Some providers store prices as floats with tiny rounding noise.
TOLERANCE = 1e-9


def verify(rows: list[IndexRow], *, sample_cap: int = SAMPLE_CAP) -> CitationVerification:
    if not rows:
        return CitationVerification()

    rng = random.Random(f"{rows[0].provider}:{rows[0].snapshot_iso}")
    sample = rng.sample(rows, k=min(sample_cap, len(rows)))

    result = CitationVerification(sampled=len(sample))
    file_cache: dict[str, Any] = {}

    for row in sample:
        try:
            doc = _load_doc(row.store_path, file_cache)
            expr = jsonpath_parse(row.json_path)
            matches = [m.value for m in expr.find(doc)]
            if not matches:
                _record_failure(result, row, reason="json_path resolved to nothing")
                continue
            resolved = _extract_price(matches[0])
            recorded = row.monthly_usd if row.cited_price_kind == "monthly" else row.hourly_usd
            if resolved is None or recorded is None:
                _record_failure(
                    result,
                    row,
                    reason=f"missing {row.cited_price_kind} price (resolved={resolved}, recorded={recorded})",
                )
                continue
            if abs(float(resolved) - float(recorded)) > TOLERANCE * max(1.0, abs(float(recorded))):
                _record_failure(
                    result,
                    row,
                    reason=f"value mismatch (json_path={resolved}, parquet={recorded})",
                )
                continue
            result.passed += 1
        except Exception as exc:
            _record_failure(result, row, reason=f"{type(exc).__name__}: {exc}")

    return result


def _load_doc(store_path: str, cache: dict[str, Any]) -> Any:
    if store_path in cache:
        return cache[store_path]
    abs_path = PROJECT_ROOT / store_path
    cache[store_path] = orjson.loads(abs_path.read_bytes())
    return cache[store_path]


def _extract_price(value: Any) -> float | None:
    """JSONPath may resolve to a primitive (Vultr monthly_cost) or to an object
    (GCP unitPrice = {units, nanos}). Normalize to a float."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        # AWS stores per-unit prices as quoted decimal strings.
        try:
            return float(value)
        except ValueError:
            return None
    if isinstance(value, dict):
        if "units" in value and "nanos" in value:
            return float(value["units"]) + float(value["nanos"]) / 1e9
        if "USD" in value:
            return _extract_price(value["USD"])
        if "value" in value:
            return _extract_price(value["value"])
    return None


def _record_failure(result: CitationVerification, row: IndexRow, *, reason: str) -> None:
    result.failed += 1
    result.failures.append(
        {
            "instance_type": row.instance_type,
            "region_native": row.region_native,
            "store_path":    row.store_path,
            "json_path":     row.json_path,
            "reason":        reason,
        }
    )
