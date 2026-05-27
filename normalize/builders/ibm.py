"""IBM index builder.

IBM publishes a global catalog plus per-plan pricing reached via a three-hop
walk that the gate already collapses into compute.json. The compute.json file
has top-level shape:

    compute.is.instance.{service_id, plans[], pricing.<plan_id>.resources[]}
    compute.is.bare-metal-server.{...}
    compute.is.dedicated-host.{...}

Each pricing.<plan_id>.resources[] entry carries one or more `(deployment_region,
metrics[])` combinations. The metric whose `metric_id` is
`part-is.instance-hours-<plan-name>` is the bare instance-hour price. Other
metrics in the same resource describe OS license bundles, SW bundles, and
reservation pricing; we filter those out for v0.

Plan names encode vCPU and RAM as `<family>-<vcpu>x<ram_gb>` (e.g. bx4-2x8 = 2
vCPU, 8 GB). GPU plans append a third segment (gx3-16x80x1l4 = 16 vCPU, 80 GB,
one L4 GPU); we parse vCPU and RAM and let the family classifier handle the
gpu tag.

v0 scope: is.instance only. Bare-metal and dedicated-host are deferred to v1.
Power Systems (power-iaas.pvm-instance) is out of v0 per ADR scope.

Per ADR 0003 the citation json_path uses the stable plan_id, deployment_region,
and metric_id in filter expressions. Dotted dict keys (`is.instance`) use
bracket-quote syntax to avoid JSONPath dot ambiguity."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import orjson

from gates._shared import PROJECT_ROOT
from normalize.builders import BuilderOutput
from normalize.fingerprint import fingerprint as fp_walk
from normalize.schema import IndexRow
from normalize.taxonomy_loader import canonical_region, classify_family

PROVIDER = "ibm"
COMPUTE_FILE = "compute.json"

# is.instance is v0; the other two are scoped out.
V0_SERVICE = "is.instance"

# Plan-name pattern: family - vcpu 'x' ram (optional GPU suffix).
PLAN_NAME_RE = re.compile(r"^(?P<family>[a-z0-9]+)-(?P<vcpu>\d+)x(?P<ram>\d+)(?:x.*)?$")

# Hourly to monthly conversion (matches Azure builder).
HOURS_PER_MONTH = 730.0


def build(snapshot_dir: Path) -> BuilderOutput:
    compute_path = snapshot_dir / COMPUTE_FILE
    doc = orjson.loads(compute_path.read_bytes())
    snapshot_iso = snapshot_dir.name
    store_path = _relpath(compute_path)
    # IBM's source_url in the receipt is templated; the actual compute slice
    # came from the catalog children + per-plan pricing endpoints. We surface
    # the catalog URL as the entry point and let the json_path resolve to the
    # specific leaf inside the per-plan pricing payload.
    source_url = "https://globalcatalog.cloud.ibm.com/api/v1?q=kind:service"

    svc = doc.get("compute", {}).get(V0_SERVICE, {})
    plans = svc.get("plans", [])
    pricing = svc.get("pricing", {})

    rows: list[IndexRow] = []
    for plan in plans:
        rows.extend(
            _rows_for_plan(
                plan=plan,
                pricing=pricing,
                snapshot_iso=snapshot_iso,
                source_url=source_url,
                store_path=store_path,
            )
        )

    return BuilderOutput(
        rows=rows,
        fingerprint=fp_walk(doc),
        source_files=[store_path],
    )


def _rows_for_plan(
    *,
    plan: dict[str, Any],
    pricing: dict[str, Any],
    snapshot_iso: str,
    source_url: str,
    store_path: str,
) -> list[IndexRow]:
    plan_name = plan.get("name") or ""
    plan_id = plan.get("id") or ""
    m = PLAN_NAME_RE.match(plan_name)
    if not m:
        return []
    vcpu = int(m.group("vcpu"))
    ram_gb = float(m.group("ram"))
    if vcpu <= 0 or ram_gb <= 0:
        return []

    family = classify_family(PROVIDER, plan_name)
    target_metric_id = f"part-is.instance-hours-{plan_name}"

    pricing_block = pricing.get(plan_id, {})
    out: list[IndexRow] = []
    seen_regions: set[str] = set()

    for resource in pricing_block.get("resources", []):
        region_native = resource.get("deployment_region") or ""
        # Some IBM deployment responses carry a DEFAULT entry; skip it (no
        # actual region to price against).
        if not region_native or region_native == "DEFAULT" or region_native in seen_regions:
            continue
        seen_regions.add(region_native)

        hourly = _find_hourly_price(resource.get("metrics", []), target_metric_id)
        if hourly is None:
            continue

        out.append(
            IndexRow(
                provider=PROVIDER,
                snapshot_iso=snapshot_iso,
                instance_type=plan_name,
                family=family,
                region_native=region_native,
                region_canonical=canonical_region(PROVIDER, region_native),
                vcpu=vcpu,
                ram_gb=ram_gb,
                hourly_usd=hourly,
                monthly_usd=hourly * HOURS_PER_MONTH,
                source_url=source_url,
                store_path=store_path,
                json_path=(
                    f"$.compute['{V0_SERVICE}'].pricing['{plan_id}']"
                    f".resources[?(@.deployment_region=='{region_native}')]"
                    f".metrics[?(@.metric_id=='{target_metric_id}')]"
                    f".amounts[?(@.country=='USA' & @.currency=='USD')]"
                    f".prices[0].price"
                ),
                cited_price_kind="hourly",
            )
        )

    return out


def _find_hourly_price(metrics: list[dict[str, Any]], target_metric_id: str) -> float | None:
    for metric in metrics:
        if metric.get("metric_id") != target_metric_id:
            continue
        for amount in metric.get("amounts", []):
            if amount.get("country") != "USA" or amount.get("currency") != "USD":
                continue
            prices = amount.get("prices", [])
            if not prices:
                return None
            price = prices[0].get("price")
            try:
                return float(price)
            except (TypeError, ValueError):
                return None
    return None


def _relpath(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))
