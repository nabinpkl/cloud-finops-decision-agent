"""GCP SKU filtering and rate-row construction."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from project_paths import TAXONOMY_DIR
from normalize.schema import IndexRow
from normalize.taxonomy.loader import canonical_region

PROVIDER = "gcp"
FLEX_RULES_PATH = TAXONOMY_DIR / "flex_rules.json"
SKIP_TOKENS = (
    "Sole Tenancy",
    "Custom",
    "Extended",
    "DWS Defined Duration",
    "Reserved",
    "Spot",
    "Sustained Use",
    "Preemptible",
    "Commit",
)
FAMILY_RE_CACHE: dict[str, re.Pattern] = {}


def maybe_rate_row(
    *,
    sku: dict[str, Any],
    family_re: re.Pattern,
    rules: dict[str, Any],
    snapshot_iso: str,
    source_url: str,
    store_path: str,
) -> IndexRow | None:
    category = sku.get("category", {})
    if category.get("resourceFamily") != "Compute":
        return None
    if category.get("usageType") != "OnDemand":
        return None

    resource_group = category.get("resourceGroup", "")
    if resource_group == "CPU":
        rate_unit = "per_vcpu_hour"
    elif resource_group == "RAM":
        rate_unit = "per_gb_ram_hour"
    else:
        return None

    description = sku.get("description", "") or ""
    if any(skip in description for skip in SKIP_TOKENS):
        return None

    match = family_re.match(description)
    if not match:
        return None
    family_key = match.group(1).lower()
    if family_key not in rules:
        return None

    region_native = first_concrete_region(sku.get("serviceRegions", []) or [])
    if region_native is None:
        return None

    pricing_info = sku.get("pricingInfo", [])
    if not pricing_info:
        return None
    rate = extract_hourly_rate(pricing_info[0])
    if rate is None or rate <= 0:
        return None

    sku_id = sku.get("skuId", "")
    if not sku_id:
        return None

    rule = rules[family_key]
    rate_short = "cpu" if rate_unit == "per_vcpu_hour" else "ram"
    return IndexRow(
        provider=PROVIDER,
        snapshot_iso=snapshot_iso,
        instance_type=f"{family_key}.{rate_short}",
        family=rule["taxonomy_family"],
        region_native=region_native,
        region_canonical=canonical_region(PROVIDER, region_native),
        vcpu=None,
        ram_gb=None,
        hourly_usd=rate,
        monthly_usd=None,
        source_url=source_url,
        store_path=store_path,
        json_path=(
            f"$.skus[?(@.skuId=='{sku_id}')]"
            f".pricingInfo[0].pricingExpression.tieredRates[0].unitPrice"
        ),
        cited_price_kind="rate_hourly",
        row_kind="rate",
        rate_unit=rate_unit,
    )


def first_concrete_region(regions: list[str]) -> str | None:
    for region in regions:
        if region in ("us", "europe", "asia", "global"):
            continue
        if "-" in region and any(char.isdigit() for char in region):
            return region
    return None


def extract_hourly_rate(pricing_info: dict[str, Any]) -> float | None:
    expr = pricing_info.get("pricingExpression", {}) or {}
    tiered = expr.get("tieredRates", []) or []
    if not tiered:
        return None
    unit_price = tiered[0].get("unitPrice", {}) or {}
    units = unit_price.get("units", "0")
    nanos = unit_price.get("nanos", 0)
    try:
        return float(units) + float(nanos) / 1e9
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=1)
def load_gcp_rules() -> dict[str, Any]:
    doc = json.loads(FLEX_RULES_PATH.read_text())
    return doc.get("gcp", {})


def family_regex(rules: dict[str, Any]) -> re.Pattern:
    key = ",".join(sorted(rules.keys()))
    cached = FAMILY_RE_CACHE.get(key)
    if cached is not None:
        return cached
    families = sorted(rules.keys(), key=lambda item: -len(item))
    pattern = r"^(" + "|".join(re.escape(family.upper()) for family in families) + r")\b"
    compiled = re.compile(pattern)
    FAMILY_RE_CACHE[key] = compiled
    return compiled
