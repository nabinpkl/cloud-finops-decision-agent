"""OVHcloud index builder.

The snapshot holds one public catalog per subsidiary: `catalog-us.json` (USD, US
regions) and `catalog-fr.json` (EUR excluding VAT; Europe, Canada and
Asia-Pacific). Each `addons[]` entry with product `publiccloud-instance` is one
flavor and billing mode. Only `<flavor>.consumption` is indexed: pay-as-you-go
hourly. Monthly commitments (`.monthly.postpaid`), Local Zone and 3-AZ prices
(`.consumption.LZ.*`, `.consumption.3AZ`, `.consumption.<zone>`), Windows flavors
and entries without a `blobs.technical` spec block (Kimsufi and bare-metal
listings) are skipped on purpose.

Prices are integers in 1e-8 of the catalog's currency (`price: 6050000` is
0.0605), so rows cite `pricings[i].price` with `cited_price_scale` 1e-8. A USD
catalog fills `hourly_usd`; an EUR catalog fills `hourly_eur` (ADR 0019). A
flavor gets one row per region in its `configurations[name=region]` list; a flavor
that lists no regions gets one row at region `global` (price known, availability
not published), outside every canonical bucket."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import orjson

from normalize.builders import HOURS_PER_MONTH, BuilderOutput, SkipTally, merged_fingerprint
from normalize.indexing.fingerprint import fingerprint as fp_walk
from normalize.schema import IndexRow
from normalize.snapshot_files import SnapshotError, snapshot_files
from normalize.taxonomy.loader import canonical_region, classify_family

PROVIDER = "ovh"
CATALOG_PREFIX = "catalog-"
INSTANCE_PRODUCT = "publiccloud-instance"
HOURLY_SUFFIX = ".consumption"
HOURLY_DESCRIPTION = "hourly prices"
PRICE_SCALE = 1e-8
NO_REGION = "global"


def build(snapshot_dir: Path) -> BuilderOutput:
    tally = SkipTally()
    rows: list[IndexRow] = []
    fingerprints: list[list[list[str]]] = []
    source_files: list[str] = []

    for catalog in snapshot_files(snapshot_dir):
        if not catalog.name.startswith(CATALOG_PREFIX):
            continue
        doc = orjson.loads(catalog.path.read_bytes())
        currency = (doc.get("locale") or {}).get("currencyCode")
        if currency not in ("USD", "EUR"):
            raise SnapshotError(f"{catalog.store_path}: catalog currency {currency!r}, expected USD or EUR")
        source_files.append(catalog.store_path)
        fingerprints.append(fp_walk(doc))
        for addon in doc.get("addons", []):
            rows.extend(
                _rows(
                    addon,
                    currency=currency,
                    snapshot_iso=snapshot_dir.name,
                    source_url=catalog.url,
                    store_path=catalog.store_path,
                    tally=tally,
                )
            )

    return tally.output(rows=rows, fingerprint=merged_fingerprint(fingerprints), source_files=source_files)


def _rows(
    addon: dict[str, Any],
    *,
    currency: str,
    snapshot_iso: str,
    source_url: str,
    store_path: str,
    tally: SkipTally,
) -> list[IndexRow]:
    plan_code = addon.get("planCode", "")
    if addon.get("product") != INSTANCE_PRODUCT or not plan_code.endswith(HOURLY_SUFFIX) or plan_code.count(".") != 1:
        return []
    technical = (addon.get("blobs") or {}).get("technical") or {}
    if not technical:
        return []
    flavor = addon.get("invoiceName") or plan_code.removesuffix(HOURLY_SUFFIX)
    if flavor.startswith("win-") or (technical.get("os") or {}).get("family") == "windows":
        return []
    tally.candidate()

    pricings = addon.get("pricings") or []
    index = next((i for i, p in enumerate(pricings) if p.get("description") == HOURLY_DESCRIPTION), None)
    if index is None or not isinstance(pricings[index].get("price"), int):
        tally.missing_field("pricings[].price")
        return []
    raw_price = pricings[index]["price"]
    if raw_price == 0:
        return []
    vcpu = (technical.get("cpu") or {}).get("cores")
    ram_gb = (technical.get("memory") or {}).get("size")
    if not isinstance(vcpu, int):
        tally.missing_field("blobs.technical.cpu.cores")
        return []
    if not isinstance(ram_gb, int | float):
        tally.missing_field("blobs.technical.memory.size")
        return []

    hourly = raw_price * PRICE_SCALE
    monthly = hourly * HOURS_PER_MONTH
    usd = currency == "USD"
    gpu = technical.get("gpu") or {}
    gpu_memory = (gpu.get("memory") or {}).get("size")
    regions = next((c.get("values") or [] for c in addon.get("configurations") or [] if c.get("name") == "region"), [])
    family = classify_family(PROVIDER, flavor)
    return [
        IndexRow(
            provider=PROVIDER,
            snapshot_iso=snapshot_iso,
            instance_type=flavor,
            family=family,
            region_native=region,
            region_canonical=canonical_region(PROVIDER, region),
            vcpu=vcpu,
            ram_gb=float(ram_gb),
            hourly_usd=hourly if usd else None,
            monthly_usd=monthly if usd else None,
            hourly_eur=None if usd else hourly,
            monthly_eur=None if usd else monthly,
            gpu_model=gpu.get("model") or None,
            gpu_count=gpu.get("number") or None,
            gpu_memory_gb=float(gpu_memory) if gpu_memory else None,
            source_url=source_url,
            store_path=store_path,
            json_path=f"$.addons[?(@.planCode=='{plan_code}')].pricings[{index}].price",
            cited_price_kind="hourly" if usd else "hourly_eur",
            cited_price_scale=PRICE_SCALE,
        )
        for region in (regions or [NO_REGION])
    ]
