"""Vultr pricing gate: single-shot fetch from the public /v2/plans endpoint."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import timedelta

import httpx

from gates._shared import (
    FileRecord,
    PROJECT_ROOT,
    emit,
    fetch_polite,
    is_fresh,
    iso_compact,
    iso_z,
    latest_receipt_path,
    now_utc,
    sha256_bytes,
    store_root,
)

PROVIDER = "vultr"
SERVICE = "plans"
PLANS_URL = "https://api.vultr.com/v2/plans"
FRESHNESS = timedelta(hours=24)

# Vultr's /v2/plans is no-auth, no-pagination, single response (~70 KB, ~100 plans).
# Each plan carries vcpu_count, ram, disk, bandwidth, monthly_cost, hourly_cost,
# type (vc2/vhf/vhp/etc.), and a locations[] list of region codes where the plan
# is available. Price is global per plan; locations[] is an availability filter,
# not a pricing dimension (same shape as Oracle and DigitalOcean).

STORE_ROOT = store_root(PROVIDER)


@dataclass
class Receipt:
    success: bool
    provider: str
    service: str
    source_url: str
    store_dir: str
    fetched_at: str
    regions_included: list[str]
    plan_count: int
    files: list[FileRecord]
    total_size_bytes: int


async def fetch_plans() -> dict:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await fetch_polite(client, PLANS_URL, timeout=120.0)
        return resp.json()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Vultr pricing snapshot.")
    parser.add_argument("--force", action="store_true", help="Bypass the 24h freshness rule.")
    args = parser.parse_args()

    if not args.force:
        latest = latest_receipt_path(PROVIDER)
        if latest is not None and is_fresh(latest, FRESHNESS):
            emit(json.loads(latest.read_text()))

    fetched_at = now_utc()
    snapshot_dir = STORE_ROOT / iso_compact(fetched_at)

    try:
        upstream = asyncio.run(fetch_plans())
    except httpx.HTTPError as exc:
        emit(
            {
                "success": False,
                "provider": PROVIDER,
                "error": str(exc),
                "hint": "Check network connectivity and that api.vultr.com is reachable.",
            },
            code=1,
        )

    plans = upstream.get("plans", [])
    regions = sorted({loc for plan in plans for loc in plan.get("locations", [])})

    snapshot_payload = {
        "fetched_at": iso_z(fetched_at),
        "source_url": PLANS_URL,
        "plan_count": len(plans),
        "plans": plans,
    }
    snapshot_bytes = json.dumps(snapshot_payload, indent=2).encode("utf-8")

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "plans.json").write_bytes(snapshot_bytes)

    files = [
        FileRecord(
            name="plans.json",
            hash=sha256_bytes(snapshot_bytes),
            size_bytes=len(snapshot_bytes),
            source_url=PLANS_URL,
        )
    ]

    receipt = Receipt(
        success=True,
        provider=PROVIDER,
        service=SERVICE,
        source_url=PLANS_URL,
        store_dir=str(snapshot_dir.relative_to(PROJECT_ROOT)),
        fetched_at=iso_z(fetched_at),
        regions_included=regions,
        plan_count=len(plans),
        files=files,
        total_size_bytes=sum(f.size_bytes for f in files),
    )

    (snapshot_dir / "receipt.json").write_text(json.dumps(asdict(receipt), indent=2))
    emit(asdict(receipt))


if __name__ == "__main__":
    main()
