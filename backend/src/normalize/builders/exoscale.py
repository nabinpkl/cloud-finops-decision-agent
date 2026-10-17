"""Exoscale index builder.

Two raw files: `instance-types.json` (the v2 API's types with family, size, cpus,
memory in bytes, gpus and zones) and `pricing-opencompute.json` (hourly prices per
currency, one price for every zone). A type's price key is `running_<size>` for
the standard family and `running_<family>_<size>` otherwise, with the size's
hyphens as underscores (extra-large is priced as extra_large). A type with no
public price (gpub300 in Oct 2026) counts as missing its price. Rows use the USD
prices Exoscale publishes, one row per zone the type runs in, named the way
Exoscale names types (`standard.medium`, `gpua30.small`).

The instance-type API gives a GPU count but not the model. Families whose name
spells the model (gpua30, gpu3080ti) fill `gpu_model`; the older gpu, gpu2 and
gpu3 families leave it null rather than guess."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import orjson

from normalize.builders import HOURS_PER_MONTH, BuilderOutput, SkipTally, merged_fingerprint
from normalize.indexing.fingerprint import fingerprint as fp_walk
from normalize.schema import IndexRow
from normalize.snapshot_files import SnapshotError, snapshot_files
from normalize.taxonomy.loader import canonical_region, classify_family

PROVIDER = "exoscale"
PRICING_FILE = "pricing-opencompute.json"
TYPES_FILE = "instance-types.json"
CURRENCY_KEY = "usd"
GIB = 1024**3
GPU_MODEL_BY_FAMILY = {
    "gpua30": "A30",
    "gpua5000": "RTX A5000",
    "gpu3080ti": "RTX 3080 Ti",
    "gpurtx6000pro": "RTX PRO 6000",
    "gpub300": "B300",
}


def build(snapshot_dir: Path) -> BuilderOutput:
    files = {f.name: f for f in snapshot_files(snapshot_dir)}
    for name in (PRICING_FILE, TYPES_FILE):
        if name not in files:
            raise SnapshotError(f"{snapshot_dir}: receipt lists no {name}")
    pricing_file = files[PRICING_FILE]
    pricing = orjson.loads(pricing_file.path.read_bytes())
    types = orjson.loads(files[TYPES_FILE].path.read_bytes())
    prices = pricing.get(CURRENCY_KEY) or {}
    tally = SkipTally()
    rows: list[IndexRow] = []

    for instance_type in types.get("instance-types", []):
        rows.extend(
            _rows(
                instance_type,
                prices=prices,
                snapshot_iso=snapshot_dir.name,
                source_url=pricing_file.url,
                store_path=pricing_file.store_path,
                tally=tally,
            )
        )

    return tally.output(
        rows=rows,
        fingerprint=merged_fingerprint([fp_walk(pricing), fp_walk(types)]),
        source_files=[pricing_file.store_path, files[TYPES_FILE].store_path],
    )


def _rows(
    instance_type: dict[str, Any],
    *,
    prices: dict[str, Any],
    snapshot_iso: str,
    source_url: str,
    store_path: str,
    tally: SkipTally,
) -> list[IndexRow]:
    family = instance_type.get("family")
    size = instance_type.get("size")
    tally.candidate()
    if not family or not size:
        tally.missing_field("family/size")
        return []
    price_size = size.replace("-", "_")  # the API's extra-large is priced as extra_large
    key = f"running_{price_size}" if family == "standard" else f"running_{family}_{price_size}"
    try:
        hourly = float(prices[key])
    except (KeyError, TypeError, ValueError):
        tally.missing_field(f"{CURRENCY_KEY}.running_*")
        return []
    vcpu = instance_type.get("cpus")
    memory = instance_type.get("memory")
    if not isinstance(vcpu, int):
        tally.missing_field("cpus")
        return []
    if not isinstance(memory, int):
        tally.missing_field("memory")
        return []
    name = f"{family}.{size}"
    gpus = instance_type.get("gpus")
    return [
        IndexRow(
            provider=PROVIDER,
            snapshot_iso=snapshot_iso,
            instance_type=name,
            family=classify_family(PROVIDER, name),
            region_native=zone,
            region_canonical=canonical_region(PROVIDER, zone),
            vcpu=vcpu,
            ram_gb=memory / GIB,
            hourly_usd=hourly,
            monthly_usd=hourly * HOURS_PER_MONTH,
            gpu_model=GPU_MODEL_BY_FAMILY.get(family) if gpus else None,
            gpu_count=gpus or None,
            source_url=source_url,
            store_path=store_path,
            json_path=f"$.{CURRENCY_KEY}.{key}",
            cited_price_kind="hourly",
        )
        for zone in instance_type.get("zones") or []
    ]
