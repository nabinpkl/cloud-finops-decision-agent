"""Azure VM row filtering, SKU parsing, and row construction."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from project_paths import TAXONOMY_DIR
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


def rows_for_region(
    *,
    doc: dict[str, Any],
    specs: dict[str, Any],
    snapshot_iso: str,
    store_path: str,
) -> list[IndexRow]:
    items = doc.get("items", [])
    source_url = doc.get("source_url", "")
    region_native = doc.get("region") or first_region(items)

    out: list[IndexRow] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if not is_linux_ondemand_vm(item):
            continue
        sku = item.get("armSkuName", "")
        parsed = parse_sku(sku, specs)
        if parsed is None:
            continue
        family_slug, vcpu, ram_gb = parsed
        key = (sku, region_native)
        if key in seen:
            continue
        seen.add(key)

        hourly = maybe_float(item.get("retailPrice"))
        if hourly is None or hourly == 0.0:
            continue
        meter_id = item.get("meterId", "")

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
                json_path=f"$.items[?(@.meterId=='{meter_id}')].retailPrice",
                cited_price_kind="hourly",
            )
        )

    return out


def is_linux_ondemand_vm(item: dict[str, Any]) -> bool:
    if item.get("serviceFamily") != "Compute":
        return False
    if item.get("serviceName") != "Virtual Machines":
        return False
    if item.get("type") != "Consumption":
        return False
    if not item.get("isPrimaryMeterRegion", False):
        return False
    product = item.get("productName", "") or ""
    meter = item.get("meterName", "") or ""
    sku_name = item.get("skuName", "") or ""
    if "Windows" in product:
        return False
    if "Spot" in meter or "Spot" in sku_name:
        return False
    if "Low Priority" in meter:
        return False
    return True


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


def first_region(items: list[dict[str, Any]]) -> str:
    for item in items:
        region = item.get("armRegionName")
        if region:
            return region
    return ""


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
