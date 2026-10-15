"""IBM index builder.

IBM publishes a global catalog plus per-plan pricing reached via a three-hop
walk. The snapshot keeps each response as fetched:

    catalog-NNNN.json                 catalog pages (services)
    children-<service>-NNNN.json      a compute service's plans, `resources[]`
    pricing-<plan_id>.json            that plan's deployment pricing

Each pricing file's resources[] entry carries one `(deployment_region,
metrics[])` combination. The metric whose `metric_id` is
`part-is.instance-hours-<plan-name>` is the bare instance-hour price. Other
metrics in the same resource describe OS license bundles, SW bundles, and
reservation pricing; we filter those out for v0.

Plan names encode vCPU and RAM as `<family>-<vcpu>x<ram_gb>` (e.g. bx4-2x8 = 2
vCPU, 8 GB). GPU plans append a third segment (gx3-16x80x1l4 = 16 vCPU, 80 GB,
one L4 GPU); we parse vCPU and RAM and let the family classifier handle the
gpu tag.

v0 scope: is.instance only. Bare-metal and dedicated-host are fetched but not
indexed. Power Systems (power-iaas.pvm-instance) is out of v0 per ADR scope.

Per ADR 0003 the citation json_path uses the stable deployment_region and
metric_id in filter expressions inside the plan's own pricing file."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import orjson

from normalize.builders import BuilderOutput, SkipTally, merged_fingerprint
from normalize.indexing.fingerprint import fingerprint as fp_walk
from normalize.schema import IndexRow
from normalize.snapshot_files import SnapshotError, SnapshotFile, snapshot_files
from normalize.taxonomy.loader import canonical_region, classify_family

PROVIDER = "ibm"

# is.instance is v0; the other two are scoped out.
V0_SERVICE = "is.instance"
CHILDREN_PREFIX = f"children-{V0_SERVICE}-"

# Plan-name pattern: family - vcpu 'x' ram (optional GPU suffix).
PLAN_NAME_RE = re.compile(r"^(?P<family>[a-z0-9]+)-(?P<vcpu>\d+)x(?P<ram>\d+)(?:x.*)?$")

# Hourly to monthly conversion (matches Azure builder).
HOURS_PER_MONTH = 730.0


def build(snapshot_dir: Path) -> BuilderOutput:
    files = {f.name: f for f in snapshot_files(snapshot_dir)}
    snapshot_iso = snapshot_dir.name
    tally = SkipTally()
    rows: list[IndexRow] = []
    source_files: list[str] = []
    fingerprints: list[list[list[str]]] = []

    for name in sorted(n for n in files if n.startswith(CHILDREN_PREFIX)):
        children = files[name]
        source_files.append(children.store_path)
        doc = orjson.loads(children.path.read_bytes())
        fingerprints.append(fp_walk(doc))
        for plan in doc.get("resources", []):
            plan_name = plan.get("name") or ""
            plan_id = plan.get("id") or ""
            m = PLAN_NAME_RE.match(plan_name)
            if not m or not plan_id:
                continue
            pricing = files.get(f"pricing-{plan_id}.json")
            if pricing is None:
                raise SnapshotError(f"{children.store_path}: plan {plan_id} has no pricing file in the receipt")
            pricing_doc = orjson.loads(pricing.path.read_bytes())
            source_files.append(pricing.store_path)
            fingerprints.append(fp_walk(pricing_doc))
            rows.extend(
                _rows_for_plan(
                    plan_name=plan_name,
                    vcpu=int(m.group("vcpu")),
                    ram_gb=float(m.group("ram")),
                    pricing_doc=pricing_doc,
                    pricing=pricing,
                    snapshot_iso=snapshot_iso,
                    tally=tally,
                )
            )

    return tally.output(rows=rows, fingerprint=merged_fingerprint(fingerprints), source_files=source_files)


def _rows_for_plan(
    *,
    plan_name: str,
    vcpu: int,
    ram_gb: float,
    pricing_doc: dict[str, Any],
    pricing: SnapshotFile,
    snapshot_iso: str,
    tally: SkipTally,
) -> list[IndexRow]:
    if vcpu <= 0 or ram_gb <= 0:
        return []

    family = classify_family(PROVIDER, plan_name)
    target_metric_id = f"part-is.instance-hours-{plan_name}"

    out: list[IndexRow] = []
    seen_regions: set[str] = set()

    for resource in pricing_doc.get("resources", []):
        region_native = resource.get("deployment_region") or ""
        # Some IBM deployment responses carry a DEFAULT entry; skip it (no
        # actual region to price against).
        if not region_native or region_native == "DEFAULT" or region_native in seen_regions:
            continue
        seen_regions.add(region_native)
        metrics = resource.get("metrics", [])
        # IBM sometimes lists only another plan's instance-hours metric for a region
        # (nxf-2x1 carried nxf-1x4's in May 2026); that region is not priced for this
        # plan, which is IBM's data, not a field we failed to read.
        if not any(metric.get("metric_id") == target_metric_id for metric in metrics):
            continue
        tally.candidate()

        hourly = _find_hourly_price(metrics, target_metric_id)
        if hourly is None:
            tally.missing_field("metrics[instance-hours].amounts[USA/USD].prices[0].price")
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
                source_url=pricing.url,
                store_path=pricing.store_path,
                json_path=(
                    f"$.resources[?(@.deployment_region=='{region_native}')]"
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
