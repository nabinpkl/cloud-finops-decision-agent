"""Azure Virtual Machines pricing gate: per-region narrow filter via the Retail Prices API."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
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

PROVIDER = "azure"
SERVICE = "virtual-machines"
RETAIL_PRICES_BASE = "https://prices.azure.com/api/retail/prices"
FRESHNESS = timedelta(hours=24)
REGIONS: list[str] = ["eastus", "westeurope", "southeastasia"]

# Azure pricing data is normalized as (SKU x region x priceType x OS) rows. An
# unfiltered "all Virtual Machines" fetch produces 500+ pages of 1000 items each
# and trips an unpublished rate limit (~470 sequential requests => 429 with
# Retry-After=60). v0 narrows to one region at a time, OnDemand only. Spot,
# Reservation, and Savings rates are out of v0; revisit when the agent answers
# commitment-shape questions.
#
# Page size is hardcoded at 1000 server-side; $top is silently ignored.

STORE_ROOT = store_root(PROVIDER)


def log(msg: str) -> None:
    print(f"[azure] {msg}", file=sys.stderr, flush=True)


def filter_for(region: str) -> str:
    return (
        f"serviceName eq 'Virtual Machines' "
        f"and priceType eq 'Consumption' "
        f"and armRegionName eq '{region}'"
    )


def first_url_for(region: str) -> str:
    return f"{RETAIL_PRICES_BASE}?$filter={filter_for(region)}"


@dataclass
class Receipt:
    success: bool
    provider: str
    service: str
    source_url: str
    store_dir: str
    fetched_at: str
    regions_included: list[str]
    item_count: int
    page_count: int
    files: list[FileRecord]
    total_size_bytes: int


async def fetch_region(client: httpx.AsyncClient, region: str) -> tuple[list[dict], int, str]:
    items: list[dict] = []
    pages = 0
    first_url = first_url_for(region)
    next_url: str | None = first_url

    def on_retry(attempt: int, status: int, wait: float) -> None:
        log(f"{region} page {pages} attempt {attempt}: {status}, waiting {wait:.1f}s")

    while next_url:
        pages += 1
        t0 = now_utc()
        resp = await fetch_polite(client, next_url, timeout=120.0, on_retry=on_retry)
        elapsed = (now_utc() - t0).total_seconds()
        log(f"{region} page {pages}: 200 in {elapsed:.2f}s")
        page = resp.json()
        items.extend(page.get("Items", []))
        next_url = page.get("NextPageLink") or None
    return items, pages, first_url


async def fetch_all_regions(regions: list[str]) -> dict[str, tuple[list[dict], int, str]]:
    results: dict[str, tuple[list[dict], int, str]] = {}
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for region in regions:
            log(f"region {region}: starting")
            items, pages, first_url = await fetch_region(client, region)
            log(f"region {region}: complete, {pages} pages, {len(items)} items")
            results[region] = (items, pages, first_url)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Azure Virtual Machines pricing snapshot.")
    parser.add_argument("--force", action="store_true", help="Bypass the 24h freshness rule.")
    args = parser.parse_args()

    if not args.force:
        latest = latest_receipt_path(PROVIDER)
        if latest is not None and is_fresh(latest, FRESHNESS):
            emit(json.loads(latest.read_text()))

    fetched_at = now_utc()
    snapshot_dir = STORE_ROOT / iso_compact(fetched_at)

    try:
        results = asyncio.run(fetch_all_regions(REGIONS))
    except httpx.HTTPError as exc:
        emit(
            {
                "success": False,
                "provider": PROVIDER,
                "error": str(exc),
                "hint": "Check network connectivity and that the Retail Prices API endpoint is reachable.",
            },
            code=1,
        )

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    files: list[FileRecord] = []
    total_items = 0
    total_pages = 0
    for region, (items, pages, first_url) in sorted(results.items()):
        payload = {
            "fetched_at": iso_z(fetched_at),
            "region": region,
            "service_filter": filter_for(region),
            "source_url": first_url,
            "page_count": pages,
            "item_count": len(items),
            "items": items,
        }
        payload_bytes = json.dumps(payload, indent=2).encode("utf-8")
        (snapshot_dir / f"{region}.json").write_bytes(payload_bytes)
        files.append(
            FileRecord(
                name=f"{region}.json",
                hash=sha256_bytes(payload_bytes),
                size_bytes=len(payload_bytes),
                source_url=first_url,
            )
        )
        total_items += len(items)
        total_pages += pages

    receipt = Receipt(
        success=True,
        provider=PROVIDER,
        service=SERVICE,
        source_url=first_url_for(REGIONS[0]),
        store_dir=str(snapshot_dir.relative_to(PROJECT_ROOT)),
        fetched_at=iso_z(fetched_at),
        regions_included=sorted(results.keys()),
        item_count=total_items,
        page_count=total_pages,
        files=files,
        total_size_bytes=sum(f.size_bytes for f in files),
    )

    (snapshot_dir / "receipt.json").write_text(json.dumps(asdict(receipt), indent=2))
    emit(asdict(receipt))


if __name__ == "__main__":
    main()
