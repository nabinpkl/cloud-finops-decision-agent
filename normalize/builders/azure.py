"""Azure index builder.

Azure publishes per-region snapshot files (eastus.json, westeurope.json,
southeastasia.json) filtered to `serviceName=Virtual Machines` + `priceType=
Consumption`. Spot and Low-Priority rows are mixed in under Consumption and
must be post-filtered by meterName. Windows rows are filtered out (Linux/no-OS
only for v0 to avoid licensing-bundled prices).

Unlike Linode/Vultr/IBM/AWS, Azure pricing rows do NOT carry vCPU or RAM. The
v0 builder parses vCPU from the documented armSkuName naming convention and
derives RAM from per-family ratios in normalize/taxonomy/azure_specs.json.
Families outside the v0 coverage set (D, E, F, L) are skipped, not flagged as
unclassified, because the absence is deliberate.

Per ADR 0003 the citation json_path uses the stable meterId (UUID) in a filter
expression."""

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

PROVIDER = "azure"

SPECS_PATH = PROJECT_ROOT / "normalize" / "taxonomy" / "azure_specs.json"

# Standard_<family-letters><vcpu>[-<constrained>][modifiers]_v<gen>
# We deliberately match `\d+` for vcpu greedily then optional constrained
# `-\d+`. Modifier letters appear between the constrained block and the version.
# Examples:
#   Standard_D4_v3       -> family=D vcpu=4 modifiers='' v=3
#   Standard_D4s_v5      -> family=D vcpu=4 modifiers='s' v=5
#   Standard_D4ds_v5     -> family=D vcpu=4 modifiers='ds' v=5
#   Standard_E16-4ds_v5  -> constrained, vcpu=16 active=4 (priced as E16)
#   Standard_E32_v4      -> family=E vcpu=32 modifiers='' v=4
#   Standard_Easv5       -> generation in family letters (no _v suffix); not v0
SKU_RE = re.compile(
    r"^Standard_"
    r"(?P<family>[A-Z]+)"
    r"(?P<vcpu>\d+)"
    r"(?:-(?P<constrained>\d+))?"
    r"(?P<modifiers>[a-z]*)"
    r"_v(?P<gen>\d+)$"
)

# Hourly to monthly conversion. Azure publishes hourly; we synthesize monthly
# for parquet uniformity using the standard 730-hour month convention.
HOURS_PER_MONTH = 730.0


def build(snapshot_dir: Path) -> BuilderOutput:
    specs = _load_specs()
    snapshot_iso = snapshot_dir.name

    rows: list[IndexRow] = []
    source_files: list[str] = []
    # Combined fingerprint across all per-region files so drift detection sees
    # the whole snapshot, not just one region.
    fp_acc: list[list[str]] = []

    for region_file in sorted(snapshot_dir.glob("*.json")):
        if region_file.name in ("receipt.json", "index_report.json", "schema_fingerprint.json"):
            continue
        doc = orjson.loads(region_file.read_bytes())
        store_path = _relpath(region_file)
        source_files.append(store_path)
        rows.extend(
            _rows_for_region(
                doc=doc,
                specs=specs,
                snapshot_iso=snapshot_iso,
                store_path=store_path,
            )
        )
        fp_acc.extend(fp_walk(doc))

    # Dedup fingerprint entries (same path may appear in each region file).
    fp_dedup = sorted({tuple(e) for e in fp_acc})
    fingerprint = [list(e) for e in fp_dedup]

    return BuilderOutput(rows=rows, fingerprint=fingerprint, source_files=source_files)


def _rows_for_region(
    *,
    doc: dict[str, Any],
    specs: dict[str, Any],
    snapshot_iso: str,
    store_path: str,
) -> list[IndexRow]:
    items = doc.get("items", [])
    source_url = doc.get("source_url", "")
    region_native = doc.get("region") or _first_region(items)

    out: list[IndexRow] = []
    seen: set[tuple[str, str]] = set()  # (armSkuName, region_native) dedup
    for item in items:
        if not _is_linux_ondemand_vm(item):
            continue
        sku = item.get("armSkuName", "")
        parsed = _parse_sku(sku, specs)
        if parsed is None:
            continue
        family_slug, vcpu, ram_gb = parsed
        key = (sku, region_native)
        if key in seen:
            continue
        seen.add(key)

        hourly = _maybe_float(item.get("retailPrice"))
        if hourly is None or hourly == 0.0:
            continue
        monthly = hourly * HOURS_PER_MONTH
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
                monthly_usd=monthly,
                source_url=source_url,
                store_path=store_path,
                json_path=f"$.items[?(@.meterId=='{meter_id}')].retailPrice",
                cited_price_kind="hourly",
            )
        )

    return out


def _is_linux_ondemand_vm(item: dict[str, Any]) -> bool:
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


def _parse_sku(sku: str, specs: dict[str, Any]) -> tuple[str, int, float] | None:
    """Return (family_slug, vcpu, ram_gb) or None if the SKU is outside v0 coverage."""
    m = SKU_RE.match(sku)
    if not m:
        return None
    family = m.group("family")
    # Exact family-key match only. Confidential-compute variants (DC, EC, DA,
    # EA) have different RAM ratios from their non-confidential parents (DC v3
    # is 1:8, D v3 is 1:4) so first-letter fallback would silently mis-spec
    # them. v0 prefers filtering over guessing; specs.json adds DC/EC/DA/EA
    # explicitly when we cover them.
    rule = specs.get(family)
    if rule is None:
        return None
    vcpu = int(m.group("vcpu"))
    if vcpu <= 0:
        return None

    ram_per_vcpu = float(rule["ram_per_vcpu_gb"])
    modifiers = m.group("modifiers") or ""
    ram_multiplier = 1.0
    for letter, multiplier in rule.get("modifiers", {}).items():
        if letter in modifiers:
            ram_multiplier *= float(multiplier)
    ram_gb = vcpu * ram_per_vcpu * ram_multiplier
    return rule["family_slug"], vcpu, ram_gb


def _first_region(items: list[dict[str, Any]]) -> str:
    for i in items:
        r = i.get("armRegionName")
        if r:
            return r
    return ""


def _maybe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _relpath(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


@lru_cache(maxsize=1)
def _load_specs() -> dict[str, Any]:
    doc = json.loads(SPECS_PATH.read_text())
    return {k: v for k, v in doc.items() if not k.startswith("_")}
