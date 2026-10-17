"""Scaleway index builder.

Scaleway's public catalog is stored as numbered pages (`products-NNNN.json`, each
`{products: [...], total_count}`) of Instances products. A server offer is listed
once per zone (`sku` = /instance/server/<offer>/<zone>), and the price can differ
by zone (POP2 does), so every zone gets its own row.

Prices are EUR only and stay EUR (ADR 0019): rows fill `hourly_eur` and
`monthly_eur`. Most offers are priced per hour; a few GPU offers are priced per
minute, which `cited_price_scale` (60) turns into the hourly column. The cited
node is `price.retail_price`, a {units, nanos} amount.

Skipped on purpose (neither candidates nor missing): products that are not
servers (snapshots, legacy SKU forms), retired offers, Windows offers (-WIN), and
free offers. GPU memory per GPU is the offer name's trailing `-<N>G`
(L4-1-24G is one 24 GB L4); Scaleway's hardware block names only the GPU type
and count."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import orjson

from normalize.builders import HOURS_PER_MONTH, BuilderOutput, SkipTally, merged_fingerprint
from normalize.indexing.fingerprint import fingerprint as fp_walk
from normalize.schema import IndexRow
from normalize.snapshot_files import snapshot_files
from normalize.taxonomy.loader import canonical_region, classify_family

PROVIDER = "scaleway"
PAGE_PREFIX = "products-"
GA_STATUS = "general_availability"
# Hours per pricing unit: the cited price times this is the hourly price.
UNIT_HOURS_SCALE = {"hour": 1.0, "minute": 60.0}
GPU_MEMORY_RE = re.compile(r"-(\d+)G$")
GIB = 1024**3


def build(snapshot_dir: Path) -> BuilderOutput:
    tally = SkipTally()
    rows: list[IndexRow] = []
    fingerprints: list[list[list[str]]] = []
    source_files: list[str] = []

    for page in snapshot_files(snapshot_dir):
        if not page.name.startswith(PAGE_PREFIX):
            continue
        doc = orjson.loads(page.path.read_bytes())
        source_files.append(page.store_path)
        fingerprints.append(fp_walk(doc))
        for product in doc.get("products", []):
            row = _row(product, snapshot_iso=snapshot_dir.name, source_url=page.url, store_path=page.store_path, tally=tally)
            if row is not None:
                rows.append(row)

    return tally.output(rows=rows, fingerprint=merged_fingerprint(fingerprints), source_files=source_files)


def _row(product: dict[str, Any], *, snapshot_iso: str, source_url: str, store_path: str, tally: SkipTally) -> IndexRow | None:
    sku = product.get("sku", "")
    parts = sku.split("/")
    if len(parts) != 5 or parts[1:3] != ["instance", "server"]:
        return None
    if product.get("status") != GA_STATUS:
        return None
    offer = (product.get("properties") or {}).get("instance", {}).get("offer_id") or product.get("product", "")
    if offer.upper().endswith("-WIN"):
        return None
    tally.candidate()

    zone = (product.get("locality") or {}).get("zone")
    if not zone:
        tally.missing_field("locality.zone")
        return None
    price = (product.get("price") or {}).get("retail_price") or {}
    if price.get("currency_code") != "EUR" or "units" not in price or "nanos" not in price:
        tally.missing_field("price.retail_price")
        return None
    unit = (product.get("unit_of_measure") or {}).get("unit")
    size = (product.get("unit_of_measure") or {}).get("size")
    if unit not in UNIT_HOURS_SCALE or not isinstance(size, int) or size <= 0:
        tally.missing_field("unit_of_measure")
        return None
    hardware = (product.get("properties") or {}).get("hardware") or {}
    vcpu = ((hardware.get("cpu") or {}).get("virtual") or {}).get("count")
    ram_bytes = (hardware.get("ram") or {}).get("size")
    if not isinstance(vcpu, int):
        tally.missing_field("properties.hardware.cpu.virtual.count")
        return None
    if not isinstance(ram_bytes, int):
        tally.missing_field("properties.hardware.ram.size")
        return None

    value = float(price["units"]) + float(price["nanos"]) / 1e9
    if value == 0:
        return None
    scale = UNIT_HOURS_SCALE[unit] / size
    hourly = value * scale
    gpu = hardware.get("gpu") or {}
    gpu_memory = GPU_MEMORY_RE.search(offer) if gpu else None
    return IndexRow(
        provider=PROVIDER,
        snapshot_iso=snapshot_iso,
        instance_type=offer,
        family=classify_family(PROVIDER, offer),
        region_native=zone,
        region_canonical=canonical_region(PROVIDER, zone),
        vcpu=vcpu,
        ram_gb=ram_bytes / GIB,
        hourly_usd=None,
        monthly_usd=None,
        hourly_eur=hourly,
        monthly_eur=hourly * HOURS_PER_MONTH,
        gpu_model=gpu.get("type") or None,
        gpu_count=gpu.get("count") or None,
        gpu_memory_gb=float(gpu_memory.group(1)) if gpu_memory else None,
        source_url=source_url,
        store_path=store_path,
        json_path=f"$.products[?(@.sku=='{sku}')].price.retail_price",
        cited_price_kind="hourly_eur",
        cited_price_scale=scale,
    )
