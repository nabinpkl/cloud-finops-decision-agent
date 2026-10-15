"""Vultr index builder.

Vultr publishes one /v2/plans catalog, stored as cursor pages
(`plans-NNNN.json`, each `{plans: [...], meta: {...}}`). Each plan carries a base monthly_cost +
hourly_cost plus a `locations[]` list (availability) plus an optional
`location_cost` dict of per-location overrides (a handful of plans price
differently in e.g. sao or syd).

For every plan we emit one row per canonical region the plan is available in
(us-east via ewr, eu-central via fra, ap-southeast via sgp). When the native
location appears in `location_cost`, the override price + override json_path are
used; otherwise the base price + base json_path. Plans available in none of the
three canonical regions are skipped for v0 (they would have no compare()
target). Free plans (monthly_cost == 0) are dropped.

Per ADR 0003 json_path uses the stable plan `id` in a filter expression."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import orjson

from normalize.builders import BuilderOutput, SkipTally, merged_fingerprint
from normalize.indexing.fingerprint import fingerprint as fp_walk
from normalize.schema import IndexRow
from normalize.snapshot_files import snapshot_files
from normalize.taxonomy.loader import classify_family, native_region

PROVIDER = "vultr"
PAGE_PREFIX = "plans-"

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
        for plan in doc.get("plans", []):
            rows.extend(
                _rows_for_plan(
                    plan,
                    snapshot_iso=snapshot_iso,
                    source_url=page.url,
                    store_path=page.store_path,
                    tally=tally,
                )
            )

    return tally.output(rows=rows, fingerprint=merged_fingerprint(fingerprints), source_files=source_files)


def _rows_for_plan(
    plan: dict[str, Any],
    *,
    snapshot_iso: str,
    source_url: str,
    store_path: str,
    tally: SkipTally,
) -> list[IndexRow]:
    tally.candidate()
    plan_id = plan.get("id", "")
    if not plan_id:
        tally.missing_field("id")
        return []
    monthly = _maybe_float(plan.get("monthly_cost"))
    hourly = _maybe_float(plan.get("hourly_cost"))
    if monthly is None:
        tally.missing_field("monthly_cost")
        return []
    if monthly == 0:  # drops free / $0 plans
        return []

    vcpu = _maybe_int(plan.get("vcpu_count"))
    ram_mb = _maybe_float(plan.get("ram"))
    if vcpu is None:
        tally.missing_field("vcpu_count")
        return []
    if ram_mb is None:
        tally.missing_field("ram")
        return []
    ram_gb = ram_mb / 1024.0  # Vultr publishes RAM in MB
    family = classify_family(PROVIDER, plan_id)
    locations = set(plan.get("locations", []))
    location_cost = plan.get("location_cost") or {}

    out: list[IndexRow] = []
    for canonical in CANONICAL_BUCKETS:
        native = native_region(PROVIDER, canonical)
        if native is None or native not in locations:
            continue

        override = location_cost.get(native)
        if override:
            o_monthly = _maybe_float(override.get("monthly_cost"))
            o_hourly = _maybe_float(override.get("hourly_cost"))
            if o_monthly is None:
                continue
            out.append(
                IndexRow(
                    provider=PROVIDER,
                    snapshot_iso=snapshot_iso,
                    instance_type=plan_id,
                    family=family,
                    region_native=native,
                    region_canonical=canonical,
                    vcpu=vcpu,
                    ram_gb=ram_gb,
                    hourly_usd=o_hourly,
                    monthly_usd=o_monthly,
                    source_url=source_url,
                    store_path=store_path,
                    json_path=f"$.plans[?(@.id=='{plan_id}')].location_cost.{native}.monthly_cost",
                    cited_price_kind="monthly",
                )
            )
        else:
            out.append(
                IndexRow(
                    provider=PROVIDER,
                    snapshot_iso=snapshot_iso,
                    instance_type=plan_id,
                    family=family,
                    region_native=native,
                    region_canonical=canonical,
                    vcpu=vcpu,
                    ram_gb=ram_gb,
                    hourly_usd=hourly,
                    monthly_usd=monthly,
                    source_url=source_url,
                    store_path=store_path,
                    json_path=f"$.plans[?(@.id=='{plan_id}')].monthly_cost",
                    cited_price_kind="monthly",
                )
            )

    return out


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
