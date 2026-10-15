"""Azure VM row filtering, SKU parsing, and row construction."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any, Literal

from project_paths import TAXONOMY_DIR
from normalize.builders import SkipTally
from normalize.schema import IndexRow
from normalize.taxonomy.loader import canonical_region

PROVIDER = "azure"
SPECS_PATH = TAXONOMY_DIR / "azure_specs.json"
HOURS_PER_MONTH = 730.0
SKU_RE = re.compile(
    r"^Standard_"
    r"(?P<family>[A-Z]+)"
    r"(?P<vcpu>\d+)"
    r"(?:-(?P<constrained>\d+))?"
    r"(?P<modifiers>[a-z]*)"
    r"_v(?P<gen>\d+)$"
)


def rows_for_page(
    *,
    items: list[dict[str, Any]],
    region_native: str,
    specs: dict[str, Any],
    snapshot_iso: str,
    source_url: str,
    store_path: str,
    seen: set[tuple[str, str, str]],
    tally: SkipTally,
) -> list[IndexRow]:
    """Rows for one raw page of one region. `seen` spans every page of the region,
    so a SKU priced on two pages yields one row."""
    out: list[IndexRow] = []
    for item in items:
        pricing = linux_vm_pricing(item)
        if pricing is None:
            continue
        tally.candidate()
        sku = item.get("armSkuName", "")
        if not sku:
            tally.missing_field("armSkuName")
            continue
        parsed = parse_sku(sku, specs)
        if parsed is None:
            continue
        family_slug, vcpu, ram_gb = parsed
        key = (sku, region_native, pricing)
        if key in seen:
            continue
        seen.add(key)

        hourly = maybe_float(item.get("retailPrice"))
        if hourly is None:
            tally.missing_field("retailPrice")
            continue
        if hourly == 0.0:
            continue
        meter_id = item.get("meterId", "")
        if not meter_id:
            tally.missing_field("meterId")
            continue

        out.append(
            IndexRow(
                provider=PROVIDER,
                snapshot_iso=snapshot_iso,
                instance_type=sku,
                family=family_slug,
                region_native=region_native,
                region_canonical=canonical_region(PROVIDER, region_native),
                vcpu=vcpu,
                ram_gb=ram_gb,
                hourly_usd=hourly,
                monthly_usd=hourly * HOURS_PER_MONTH,
                source_url=source_url,
                store_path=store_path,
                json_path=f"$.Items[?(@.meterId=='{meter_id}')].retailPrice",
                cited_price_kind="hourly",
                pricing=pricing,
            )
        )

    return out


def linux_vm_pricing(item: dict[str, Any]) -> Literal["on_demand", "spot"] | None:
    """"on_demand" or "spot" for a Linux VM meter we price, None for anything else
    (Windows, low priority, non-VM, non-primary meter regions). Spot meters are the
    Consumption items whose meterName or skuName ends in "Spot"."""
    if item.get("serviceFamily") != "Compute":
        return None
    if item.get("serviceName") != "Virtual Machines":
        return None
    if item.get("type") != "Consumption":
        return None
    if not item.get("isPrimaryMeterRegion", False):
        return None
    product = item.get("productName", "") or ""
    meter = item.get("meterName", "") or ""
    sku_name = item.get("skuName", "") or ""
    if "Windows" in product:
        return None
    if "Low Priority" in meter:
        return None
    if meter.endswith(" Spot") or sku_name.endswith(" Spot"):
        return "spot"
    if "Spot" in meter or "Spot" in sku_name:
        return None
    return "on_demand"


def parse_sku(sku: str, specs: dict[str, Any]) -> tuple[str, int, float] | None:
    match = SKU_RE.match(sku)
    if not match:
        return None
    rule = specs.get(match.group("family"))
    if rule is None:
        return None
    vcpu = int(match.group("vcpu"))
    if vcpu <= 0:
        return None

    ram_per_vcpu = float(rule["ram_per_vcpu_gb"])
    modifiers = match.group("modifiers") or ""
    ram_multiplier = 1.0
    for letter, multiplier in rule.get("modifiers", {}).items():
        if letter in modifiers:
            ram_multiplier *= float(multiplier)
    return rule["family_slug"], vcpu, vcpu * ram_per_vcpu * ram_multiplier


def maybe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=1)
def load_specs() -> dict[str, Any]:
    doc = json.loads(SPECS_PATH.read_text())
    return {key: value for key, value in doc.items() if not key.startswith("_")}
