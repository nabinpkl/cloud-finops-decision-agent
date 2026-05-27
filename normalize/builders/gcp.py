"""GCP index builder.

GCP's Cloud Billing Catalog prices Compute Engine per resource: one SKU per
(family, region) for CPU at a per-vCPU/hour rate, plus a sibling SKU for RAM at
a per-GB/hour rate. Predefined shapes (n2-standard-4 etc.) use these same
rates. There is no per-instance SKU in the pricing API.

Per ADR 0007 we emit rate rows: one parquet row per (family, region,
resource_group). compare() composes per-instance prices at query time using
normalize/taxonomy/flex_rules.json.

Filter:
- category.resourceFamily == "Compute"
- category.resourceGroup IN ("CPU", "RAM")
- category.usageType == "OnDemand"
- description: NOT containing "Sole Tenancy", "Custom", "DWS Defined Duration",
  "Reserved", "Spot", "Sustained Use Discount", "Preemptible"
- description matches a known family from flex_rules.json

Per ADR 0003 json_path uses the stable skuId.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import orjson

from gates._shared import PROJECT_ROOT
from normalize.builders import BuilderOutput
from normalize.fingerprint import fingerprint as fp_walk
from normalize.schema import IndexRow
from normalize.taxonomy_loader import canonical_region

PROVIDER = "gcp"
SKUS_FILE = "skus.json"

FLEX_RULES_PATH = PROJECT_ROOT / "normalize" / "taxonomy" / "flex_rules.json"

# Descriptions we want look like:
#   "N2 Instance Core running in Frankfurt"
#   "N2D AMD Instance Core running in Salt Lake City"
#   "C4A Arm Instance Core running in Mexico"
#   "M3 Memory-optimized Instance Core running in Phoenix"
#
# Descriptions we skip (premium/variant pricing handled elsewhere):
#   "Sole Tenancy Premium for ..."
#   "{family} Custom Instance Core ..."
#   "DWS Defined Duration ..."
#   "Reserved ..."
SKIP_TOKENS = (
    "Sole Tenancy",
    "Custom",         # catches "Custom Instance", "Custom Extended Instance", "AMD Custom", etc.
    "Extended",       # belt-and-braces against "Extended Instance Ram" sub-tiers we may not have seen
    "DWS Defined Duration",
    "Reserved",
    "Spot",
    "Sustained Use",
    "Preemptible",
    "Commit",
)

# Family token must be the leading word of the description (case-sensitive in
# the source). The keys in flex_rules.json are lowercase; we uppercase for the
# regex match against the GCP description.
FAMILY_RE_CACHE: dict[str, re.Pattern] = {}


def build(snapshot_dir: Path) -> BuilderOutput:
    skus_path = snapshot_dir / SKUS_FILE
    doc = orjson.loads(skus_path.read_bytes())
    snapshot_iso = snapshot_dir.name
    store_path = _relpath(skus_path)
    source_url = doc.get(
        "source_url",
        "https://cloudbilling.googleapis.com/v1/services/6F81-5844-456A/skus",
    )

    skus = doc.get("skus", [])
    rules = _load_gcp_rules()
    family_re = _family_regex(rules)
    rows: list[IndexRow] = []

    for sku in skus:
        row = _maybe_row(
            sku=sku,
            family_re=family_re,
            rules=rules,
            snapshot_iso=snapshot_iso,
            source_url=source_url,
            store_path=store_path,
        )
        if row is not None:
            rows.append(row)

    return BuilderOutput(rows=rows, fingerprint=fp_walk(doc), source_files=[store_path])


def _maybe_row(
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
        return None  # skip GPU, TPU, LocalSSD, etc.

    description = sku.get("description", "") or ""
    if any(skip in description for skip in SKIP_TOKENS):
        return None

    m = family_re.match(description)
    if not m:
        return None
    family_token = m.group(1)
    family_key = family_token.lower()
    if family_key not in rules:
        return None

    service_regions = sku.get("serviceRegions", []) or []
    # Skip aggregate region pseudo-codes like "asia", "europe", "us", "global".
    region_native = _first_concrete_region(service_regions)
    if region_native is None:
        return None

    pricing_info = sku.get("pricingInfo", [])
    if not pricing_info:
        return None
    rate = _extract_hourly_rate(pricing_info[0])
    if rate is None or rate <= 0:
        return None

    sku_id = sku.get("skuId", "")
    if not sku_id:
        return None

    rule = rules[family_key]
    # Rate rows have no concrete instance_type. Use a compact "<family>.<short>"
    # marker so the parquet row is identifiable and grep-friendly. compare()
    # builds the user-facing name from the flex_rules custom_name_template.
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


def _first_concrete_region(regions: list[str]) -> str | None:
    """Return the first region code that looks like a real region, skipping
    aggregate pseudo-codes ('us', 'europe', 'asia', 'global')."""
    for r in regions:
        if r in ("us", "europe", "asia", "global"):
            continue
        # Concrete codes have a hyphen and a digit (us-east4, europe-west3).
        if "-" in r and any(c.isdigit() for c in r):
            return r
    return None


def _extract_hourly_rate(pricing_info: dict[str, Any]) -> float | None:
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
def _load_gcp_rules() -> dict[str, Any]:
    doc = json.loads(FLEX_RULES_PATH.read_text())
    return doc.get("gcp", {})


def _family_regex(rules: dict[str, Any]) -> re.Pattern:
    key = ",".join(sorted(rules.keys()))
    cached = FAMILY_RE_CACHE.get(key)
    if cached is not None:
        return cached
    # Longest first so 'n2d' beats 'n2' on the prefix match.
    families = sorted(rules.keys(), key=lambda k: -len(k))
    pattern = r"^(" + "|".join(re.escape(f.upper()) for f in families) + r")\b"
    compiled = re.compile(pattern)
    FAMILY_RE_CACHE[key] = compiled
    return compiled


def _relpath(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))
