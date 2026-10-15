"""Linode index builder.

Linode publishes one /v4/linode/types catalog, stored as numbered pages
(`types-NNNN.json`, each `{data: [...], page, pages, results}`), with a base
price plus optional per-region overrides under data[].region_prices[]. For every type we emit:
- one base-price row keyed at the canonical 'us-east' / 'eu-central' / 'ap-southeast'
  region the base price applies to (we emit a row per canonical bucket since the
  base price is global), and
- one row per explicit region_prices[] override (currently br-gru and id-cgk).

Memory is published in MB; we convert to GB. Plans with zero monthly_cost
(the legacy 'g6-nanode-1-free' tier) are dropped.

Per ADR 0003 the citation json_path uses the stable type id and optional
region id in filter expressions, never array positions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import orjson

from normalize.builders import BuilderOutput, SkipTally, merged_fingerprint
from normalize.indexing.fingerprint import fingerprint as fp_walk
from normalize.schema import IndexRow
from normalize.snapshot_files import snapshot_files
from normalize.taxonomy.loader import canonical_region, classify_family

PROVIDER = "linode"
PAGE_PREFIX = "types-"

# v0 canonical buckets where we want a base-price row even if Linode does not
# split by region. (Linode publishes one global price; we surface it at every
# bucket so cross-provider compare() can find it.) The native code lookup is
# done via taxonomy_loader so the mapping stays in one place.
CANONICAL_BUCKETS = ["us-east", "eu-central", "ap-southeast"]


def build(snapshot_dir: Path) -> BuilderOutput:
    snapshot_iso = snapshot_dir.name
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
        for t in doc.get("data", []):
            rows.extend(
                _rows_for_type(
                    t,
                    snapshot_iso=snapshot_iso,
                    source_url=page.url,
                    store_path=page.store_path,
                    tally=tally,
                )
            )

    return tally.output(rows=rows, fingerprint=merged_fingerprint(fingerprints), source_files=source_files)


def _rows_for_type(
    t: dict[str, Any],
    *,
    snapshot_iso: str,
    source_url: str,
    store_path: str,
    tally: SkipTally,
) -> list[IndexRow]:
    tally.candidate()
    type_id = t.get("id", "")
    vcpu = _maybe_int(t.get("vcpus"))
    memory = _maybe_float(t.get("memory"))
    base_price = t.get("price") or {}
    base_hourly = _maybe_float(base_price.get("hourly"))
    base_monthly = _maybe_float(base_price.get("monthly"))
    if not type_id:
        tally.missing_field("id")
        return []
    if vcpu is None:
        tally.missing_field("vcpus")
        return []
    if memory is None:
        tally.missing_field("memory")
        return []
    if base_monthly is None:
        tally.missing_field("price.monthly")
        return []
    ram_gb = memory / 1024.0  # Linode publishes memory in MB
    family = classify_family(PROVIDER, type_id)

    out: list[IndexRow] = []

    # Base-price rows: one per canonical bucket Linode operates in. We do NOT
    # emit a row for a region that has a region_prices[] override (handled below)
    # since the override takes precedence.
    overrides = {rp["id"]: rp for rp in t.get("region_prices", []) if "id" in rp}
    if base_hourly is not None and base_monthly is not None:
        for canonical in CANONICAL_BUCKETS:
            native = _native_for(canonical)
            if native is None or native in overrides:
                continue
            out.append(
                IndexRow(
                    provider=PROVIDER,
                    snapshot_iso=snapshot_iso,
                    instance_type=type_id,
                    family=family,
                    region_native=native,
                    region_canonical=canonical,
                    vcpu=vcpu,
                    ram_gb=ram_gb,
                    hourly_usd=base_hourly,
                    monthly_usd=base_monthly,
                    source_url=source_url,
                    store_path=store_path,
                    # Base price lives at data[?(@.id==<id>)].price.monthly
                    json_path=f"$.data[?(@.id=='{type_id}')].price.monthly",
                    cited_price_kind="monthly",
                )
            )

    # Override rows: one per region_prices[] entry. The canonical region may be
    # None if the override is for a region outside our 3 v0 buckets.
    for region_id, rp in overrides.items():
        hourly = _maybe_float(rp.get("hourly"))
        monthly = _maybe_float(rp.get("monthly"))
        if hourly is None or monthly is None:
            continue
        out.append(
            IndexRow(
                provider=PROVIDER,
                snapshot_iso=snapshot_iso,
                instance_type=type_id,
                family=family,
                region_native=region_id,
                region_canonical=canonical_region(PROVIDER, region_id),
                vcpu=vcpu,
                ram_gb=ram_gb,
                hourly_usd=hourly,
                monthly_usd=monthly,
                source_url=source_url,
                store_path=store_path,
                json_path=(
                    f"$.data[?(@.id=='{type_id}')]"
                    f".region_prices[?(@.id=='{region_id}')].monthly"
                ),
                cited_price_kind="monthly",
            )
        )

    return out


def _native_for(canonical: str) -> str | None:
    from normalize.taxonomy.loader import native_region
    return native_region(PROVIDER, canonical)


def _maybe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _maybe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
