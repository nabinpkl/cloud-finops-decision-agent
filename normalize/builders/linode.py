"""Linode index builder.

Linode publishes one /v4/linode/types catalog with a base price plus optional
per-region overrides under types[].region_prices[]. For every type we emit:
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

from normalize.builders import BuilderOutput
from normalize.fingerprint import fingerprint as fp_walk
from normalize.schema import IndexRow
from normalize.taxonomy_loader import canonical_region, classify_family

PROVIDER = "linode"
TYPES_FILE = "types.json"

# v0 canonical buckets where we want a base-price row even if Linode does not
# split by region. (Linode publishes one global price; we surface it at every
# bucket so cross-provider compare() can find it.) The native code lookup is
# done via taxonomy_loader so the mapping stays in one place.
CANONICAL_BUCKETS = ["us-east", "eu-central", "ap-southeast"]


def build(snapshot_dir: Path) -> BuilderOutput:
    types_path = snapshot_dir / TYPES_FILE
    raw_bytes = types_path.read_bytes()
    doc = orjson.loads(raw_bytes)
    types_ = doc.get("types", [])

    snapshot_iso = snapshot_dir.name
    source_url = doc.get("source_url", "")
    store_path = _relpath(types_path)

    rows: list[IndexRow] = []
    for t in types_:
        rows.extend(_rows_for_type(t, snapshot_iso=snapshot_iso, source_url=source_url, store_path=store_path))

    return BuilderOutput(
        rows=rows,
        fingerprint=fp_walk(doc),
        source_files=[store_path],
    )


def _rows_for_type(
    t: dict[str, Any],
    *,
    snapshot_iso: str,
    source_url: str,
    store_path: str,
) -> list[IndexRow]:
    type_id = t.get("id", "")
    vcpu = int(t.get("vcpus", 0))
    ram_gb = float(t.get("memory", 0)) / 1024.0  # Linode publishes memory in MB
    family = classify_family(PROVIDER, type_id)
    base_price = t.get("price") or {}
    base_hourly = _maybe_float(base_price.get("hourly"))
    base_monthly = _maybe_float(base_price.get("monthly"))

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
                    # Base price lives at types[?(@.id==<id>)].price.monthly
                    json_path=f"$.types[?(@.id=='{type_id}')].price.monthly",
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
                    f"$.types[?(@.id=='{type_id}')]"
                    f".region_prices[?(@.id=='{region_id}')].monthly"
                ),
                cited_price_kind="monthly",
            )
        )

    return out


def _native_for(canonical: str) -> str | None:
    from normalize.taxonomy_loader import native_region
    return native_region(PROVIDER, canonical)


def _maybe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _relpath(path: Path) -> str:
    from gates._shared import PROJECT_ROOT
    return str(path.relative_to(PROJECT_ROOT))
