"""Vultr index builder.

Vultr publishes one /v2/plans catalog. Each plan carries a base monthly_cost +
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

from normalize.builders import BuilderOutput
from normalize.indexing.fingerprint import fingerprint as fp_walk
from normalize.schema import IndexRow
from normalize.taxonomy.loader import classify_family, native_region

PROVIDER = "vultr"
PLANS_FILE = "plans.json"

CANONICAL_BUCKETS = ["us-east", "eu-central", "ap-southeast"]


def build(snapshot_dir: Path) -> BuilderOutput:
    plans_path = snapshot_dir / PLANS_FILE
    doc = orjson.loads(plans_path.read_bytes())
    plans = doc.get("plans", [])

    snapshot_iso = snapshot_dir.name
    source_url = doc.get("source_url", "")
    store_path = _relpath(plans_path)

    rows: list[IndexRow] = []
    for p in plans:
        rows.extend(_rows_for_plan(p, snapshot_iso=snapshot_iso, source_url=source_url, store_path=store_path))

    return BuilderOutput(
        rows=rows,
        fingerprint=fp_walk(doc),
        source_files=[store_path],
    )


def _rows_for_plan(
    plan: dict[str, Any],
    *,
    snapshot_iso: str,
    source_url: str,
    store_path: str,
) -> list[IndexRow]:
    plan_id = plan.get("id", "")
    monthly = _maybe_float(plan.get("monthly_cost"))
    hourly = _maybe_float(plan.get("hourly_cost"))
    if not monthly:  # drops free / $0 plans
        return []

    vcpu = int(plan.get("vcpu_count", 0))
    ram_gb = float(plan.get("ram", 0)) / 1024.0  # Vultr publishes RAM in MB
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


def _relpath(path: Path) -> str:
    from gates._shared import PROJECT_ROOT
    return str(path.relative_to(PROJECT_ROOT))
