"""AWS index builder.

AWS publishes per-region snapshots (us-east-1.json, eu-central-1.json,
ap-southeast-1.json) from the bulk Price List API. Each file is large (~400 MB)
because it contains every SKU AWS prices in the region across all product
families, OS variants, tenancies, and term types (OnDemand + Reserved).

Structure:
  products[<sku>].attributes  -> instanceType, vcpu, memory, instanceFamily,
                                 instanceFamilyCategory, regionCode, operating-
                                 System, tenancy, preInstalledSw,
                                 licenseModel, capacitystatus
  terms.OnDemand[<sku>][<offer_code>].priceDimensions[<rate_code>].pricePerUnit.USD
      -> per-unit price as a quoted decimal string

We filter to canonical Linux on-demand pricing:
  productFamily == 'Compute Instance' (excludes bare-metal VMs for v0)
  operatingSystem == 'Linux'
  tenancy == 'Shared'
  preInstalledSw == 'NA'
  capacitystatus == 'Used'
  licenseModel == 'No License required'

Memory comes as a string like '16 GiB'; we parse it. AWS stores the price as a
string under terms.OnDemand[<sku>].*.priceDimensions.*.pricePerUnit.USD, so the
verifier (and the json_path) both expect a string-coerced numeric.

Per ADR 0003 the citation json_path uses the stable 16-char SKU."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import orjson

from gates._shared import PROJECT_ROOT
from normalize.builders import BuilderOutput
from normalize.indexing.fingerprint import fingerprint as fp_walk
from normalize.schema import IndexRow
from normalize.taxonomy.loader import canonical_region, classify_family

PROVIDER = "aws"

HOURS_PER_MONTH = 730.0

# Memory strings are like '16 GiB', '1,024 GiB', '0.5 GiB', occasionally '1 TiB'.
MEMORY_RE = re.compile(r"^\s*([0-9]+(?:[.,][0-9]+)?)\s*(GiB|TiB|MiB)\s*$", re.IGNORECASE)
MEMORY_UNIT_TO_GB = {"gib": 1.0, "tib": 1024.0, "mib": 1.0 / 1024.0}


def build(snapshot_dir: Path) -> BuilderOutput:
    snapshot_iso = snapshot_dir.name
    rows: list[IndexRow] = []
    source_files: list[str] = []
    fp_acc: list[list[str]] = []

    for region_file in sorted(snapshot_dir.glob("*.json")):
        if region_file.name in ("receipt.json", "index_report.json", "schema_fingerprint.json", "region_index.json"):
            continue
        doc = orjson.loads(region_file.read_bytes())
        store_path = _relpath(region_file)
        source_files.append(store_path)
        rows.extend(_rows_for_region(doc=doc, snapshot_iso=snapshot_iso, store_path=store_path))
        fp_acc.extend(fp_walk(doc))

    fp_dedup = sorted({tuple(e) for e in fp_acc})
    fingerprint = [list(e) for e in fp_dedup]

    return BuilderOutput(rows=rows, fingerprint=fingerprint, source_files=source_files)


def _rows_for_region(
    *,
    doc: dict[str, Any],
    snapshot_iso: str,
    store_path: str,
) -> list[IndexRow]:
    products = doc.get("products", {})
    terms = doc.get("terms", {}).get("OnDemand", {})
    source_url = (
        "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/"
        + Path(store_path).name.replace(".json", "")
        + "/index.json"
    )

    out: list[IndexRow] = []
    for sku, product in products.items():
        if not _is_linux_ondemand_vm(product):
            continue
        attrs = product["attributes"]
        instance_type = attrs.get("instanceType", "")
        if not instance_type:
            continue

        vcpu = _parse_int(attrs.get("vcpu"))
        ram_gb = _parse_memory(attrs.get("memory"))
        if vcpu is None or vcpu <= 0 or ram_gb is None or ram_gb <= 0:
            continue

        hourly = _resolve_ondemand_price(terms.get(sku, {}))
        if hourly is None or hourly == 0.0:
            continue

        region_native = attrs.get("regionCode") or ""
        family = classify_family(PROVIDER, instance_type)

        out.append(
            IndexRow(
                provider=PROVIDER,
                snapshot_iso=snapshot_iso,
                instance_type=instance_type,
                family=family,
                region_native=region_native,
                region_canonical=canonical_region(PROVIDER, region_native),
                vcpu=vcpu,
                ram_gb=ram_gb,
                hourly_usd=hourly,
                monthly_usd=hourly * HOURS_PER_MONTH,
                source_url=source_url,
                store_path=store_path,
                json_path=f"$.terms.OnDemand['{sku}'].*.priceDimensions.*.pricePerUnit.USD",
                cited_price_kind="hourly",
            )
        )

    return out


def _is_linux_ondemand_vm(product: dict[str, Any]) -> bool:
    if product.get("productFamily") != "Compute Instance":
        return False
    a = product.get("attributes", {})
    return (
        a.get("operatingSystem") == "Linux"
        and a.get("tenancy") == "Shared"
        and a.get("preInstalledSw") == "NA"
        and a.get("capacitystatus") == "Used"
        and a.get("licenseModel") == "No License required"
    )


def _resolve_ondemand_price(sku_terms: dict[str, Any]) -> float | None:
    """Walk terms.OnDemand[sku].<offer_code>.priceDimensions.<rate_code>.pricePerUnit.USD.
    There is typically exactly one offer code and one priceDimension per SKU; if
    there are multiple, we take the first non-zero hourly rate."""
    for offer in sku_terms.values():
        for rate in offer.get("priceDimensions", {}).values():
            unit = (rate.get("unit") or "").lower()
            if unit not in ("hrs", "hour"):
                continue
            ppu = rate.get("pricePerUnit", {})
            raw = ppu.get("USD")
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
    return None


def _parse_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip().replace(",", ""))
    except ValueError:
        return None


def _parse_memory(value: Any) -> float | None:
    if value is None:
        return None
    m = MEMORY_RE.match(str(value))
    if not m:
        return None
    number = float(m.group(1).replace(",", ""))
    unit = m.group(2).lower()
    return number * MEMORY_UNIT_TO_GB[unit]


def _relpath(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))
