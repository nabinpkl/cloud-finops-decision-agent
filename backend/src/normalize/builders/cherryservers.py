"""Cherry Servers index builder.

`plans.json` is the /v1/plans array fetched in USD: bare metal servers, VPS and
VDS. Each plan's `pricing[]` has one entry per billing unit. The on-demand row
cites the `Monthly` price and carries the `Hourly` one beside it (a plan without
a monthly price cites its hourly price, monthly = hourly x 730). Bare metal plans
that also publish `Spot hourly` get a spot row as well.

vCPU is the plan's thread count (`specs.cpus.threads`, which for a two-socket
server is the total across sockets), RAM is `specs.memory.total` in GB. Price is
global per plan; `available_regions[]` is availability, so each listed region
gets a row."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import orjson

from normalize.builders import HOURS_PER_MONTH, BuilderOutput, SkipTally
from normalize.indexing.fingerprint import fingerprint as fp_walk
from normalize.schema import IndexRow
from normalize.snapshot_files import SnapshotError, snapshot_files
from normalize.taxonomy.loader import canonical_region, classify_family

PROVIDER = "cherryservers"
PLANS_FILE = "plans.json"
CURRENCY = "USD"


def build(snapshot_dir: Path) -> BuilderOutput:
    files = [f for f in snapshot_files(snapshot_dir) if f.name == PLANS_FILE]
    if not files:
        raise SnapshotError(f"{snapshot_dir}: receipt lists no {PLANS_FILE}")
    plans_file = files[0]
    plans = orjson.loads(plans_file.path.read_bytes())
    tally = SkipTally()
    rows: list[IndexRow] = []
    for plan in plans:
        rows.extend(
            _rows(plan, snapshot_iso=snapshot_dir.name, source_url=plans_file.url, store_path=plans_file.store_path, tally=tally)
        )
    return tally.output(rows=rows, fingerprint=fp_walk(plans), source_files=[plans_file.store_path])


def _rows(plan: dict[str, Any], *, snapshot_iso: str, source_url: str, store_path: str, tally: SkipTally) -> list[IndexRow]:
    tally.candidate()
    slug = plan.get("slug")
    if not slug:
        tally.missing_field("slug")
        return []
    specs = plan.get("specs") or {}
    vcpu = (specs.get("cpus") or {}).get("threads")
    ram_gb = (specs.get("memory") or {}).get("total")
    if not isinstance(vcpu, int):
        tally.missing_field("specs.cpus.threads")
        return []
    if not isinstance(ram_gb, int | float):
        tally.missing_field("specs.memory.total")
        return []
    prices = {p.get("unit"): p.get("price") for p in plan.get("pricing") or [] if p.get("currency") == CURRENCY}
    hourly = _price(prices.get("Hourly"))
    monthly = _price(prices.get("Monthly"))
    if hourly is None and monthly is None:
        tally.missing_field("pricing[Hourly|Monthly]")
        return []

    base = f"$[?(@.slug=='{slug}')].pricing"
    markets: list[tuple[Literal["on_demand", "spot"], float | None, float, str, Literal["hourly", "monthly"]]] = []
    if monthly is not None:
        markets.append(("on_demand", hourly, monthly, f"{base}[?(@.unit=='Monthly')].price", "monthly"))
    elif hourly is not None:
        markets.append(("on_demand", hourly, hourly * HOURS_PER_MONTH, f"{base}[?(@.unit=='Hourly')].price", "hourly"))
    spot = _price(prices.get("Spot hourly"))
    if spot is not None:
        markets.append(("spot", spot, spot * HOURS_PER_MONTH, f"{base}[?(@.unit=='Spot hourly')].price", "hourly"))

    family = classify_family(PROVIDER, slug)
    regions = [r["slug"] for r in plan.get("available_regions") or [] if r.get("slug")]
    return [
        IndexRow(
            provider=PROVIDER,
            snapshot_iso=snapshot_iso,
            instance_type=slug,
            family=family,
            region_native=region,
            region_canonical=canonical_region(PROVIDER, region),
            vcpu=vcpu,
            ram_gb=float(ram_gb),
            hourly_usd=market_hourly,
            monthly_usd=market_monthly,
            pricing=market,
            source_url=source_url,
            store_path=store_path,
            json_path=json_path,
            cited_price_kind=kind,
        )
        for market, market_hourly, market_monthly, json_path, kind in markets
        for region in regions
    ]


def _price(value: Any) -> float | None:
    if isinstance(value, int | float) and value > 0:
        return float(value)
    return None
