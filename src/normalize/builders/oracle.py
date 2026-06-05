"""Oracle index builder.

Oracle's List Prices API publishes one global price per SKU (no per-region
split). For Flex shapes (E3/E4/E5/E6 AMD, A1/A2/A4 Ampere ARM, X9 Intel) the
pricing is split into two SKUs per family: one for OCPU at a per-OCPU/hour
rate, one for Memory at a per-GB/hour rate. Older fixed shapes (E2, X5, X7, B1)
have a single OCPU SKU and a built-in memory ratio; they are deferred to v1 in
favor of the Flex families per ADR 0006.

Per ADR 0007 we emit rate rows. region_native = "global", region_canonical =
null; compare() treats Oracle's rate rows as available in any canonical bucket
since the upstream price does not vary by region. The agent's prose surfaces
"Oracle list price is global; region is availability only" alongside results.

Per ADR 0003 the citation json_path uses the stable partNumber."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import orjson

from gates._shared import PROJECT_ROOT, TAXONOMY_DIR
from normalize.builders import BuilderOutput
from normalize.indexing.fingerprint import fingerprint as fp_walk
from normalize.schema import IndexRow

PROVIDER = "oracle"
PRODUCTS_FILE = "products.json"

FLEX_RULES_PATH = TAXONOMY_DIR / "flex_rules.json"
SOURCE_URL = "https://www.oracle.com/cloud/price-list/"

# displayName patterns to extract (family, resource):
#   "Compute - Standard - E5 - OCPU"
#   "Compute - Standard - E5 - Memory"
#   "Compute - Standard - A2 OCPU"          (no hyphen before resource)
#   "OCI - Compute - Standard - E6 - OCPU"
#   "OCI - Compute - Standard - X12 Ax - OCPU"  (Ax sub-variant; we'll skip these for v0)
DISPLAY_RE = re.compile(
    r"Standard\s*-\s*(?P<family>[A-Z]\d+|[A-Z]\d+[A-Za-z]*)\s*-?\s*(?P<resource>OCPU|Memory)\s*$"
)


def build(snapshot_dir: Path) -> BuilderOutput:
    products_path = snapshot_dir / PRODUCTS_FILE
    doc = orjson.loads(products_path.read_bytes())
    snapshot_iso = snapshot_dir.name
    store_path = _relpath(products_path)
    rules = _load_oracle_rules()

    rows: list[IndexRow] = []
    for item in doc.get("items", []):
        row = _maybe_row(
            item=item,
            rules=rules,
            snapshot_iso=snapshot_iso,
            store_path=store_path,
        )
        if row is not None:
            rows.append(row)

    return BuilderOutput(rows=rows, fingerprint=fp_walk(doc), source_files=[store_path])


def _maybe_row(
    *,
    item: dict[str, Any],
    rules: dict[str, Any],
    snapshot_iso: str,
    store_path: str,
) -> IndexRow | None:
    if item.get("serviceCategory") != "Compute - Virtual Machine":
        return None
    display = item.get("displayName", "") or ""
    m = DISPLAY_RE.search(display)
    if not m:
        return None
    family_token = m.group("family")
    if family_token not in rules:
        return None  # legacy (E2, X5, X7) or unmapped sub-variant (X12 Ax)
    resource = m.group("resource")  # "OCPU" or "Memory"

    metric = (item.get("metricName") or "").lower()
    if resource == "OCPU" and "ocpu" not in metric:
        return None
    if resource == "Memory" and "gigabyte" not in metric:
        return None

    rate = _extract_usd_payg(item.get("currencyCodeLocalizations", []))
    if rate is None or rate <= 0:
        return None

    part_number = item.get("partNumber", "")
    if not part_number:
        return None

    rate_unit = "per_ocpu_hour" if resource == "OCPU" else "per_gb_ram_hour"
    rate_short = "ocpu" if rate_unit == "per_ocpu_hour" else "ram"
    rule = rules[family_token]

    return IndexRow(
        provider=PROVIDER,
        snapshot_iso=snapshot_iso,
        instance_type=f"{family_token}.{rate_short}",
        family=rule["taxonomy_family"],
        region_native="global",  # Oracle list price is global; see module docstring
        region_canonical=None,
        vcpu=None,
        ram_gb=None,
        hourly_usd=rate,
        monthly_usd=None,
        source_url=SOURCE_URL,
        store_path=store_path,
        json_path=(
            f"$.items[?(@.partNumber=='{part_number}')]"
            f".currencyCodeLocalizations[?(@.currencyCode=='USD')]"
            f".prices[?(@.model=='PAY_AS_YOU_GO')].value"
        ),
        cited_price_kind="rate_hourly",
        row_kind="rate",
        rate_unit=rate_unit,
    )


def _extract_usd_payg(localizations: list[dict[str, Any]]) -> float | None:
    for loc in localizations:
        if loc.get("currencyCode") != "USD":
            continue
        for price in loc.get("prices", []):
            if price.get("model") != "PAY_AS_YOU_GO":
                continue
            try:
                return float(price.get("value"))
            except (TypeError, ValueError):
                continue
    return None


@lru_cache(maxsize=1)
def _load_oracle_rules() -> dict[str, Any]:
    doc = json.loads(FLEX_RULES_PATH.read_text())
    return doc.get("oracle", {})


def _relpath(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))
