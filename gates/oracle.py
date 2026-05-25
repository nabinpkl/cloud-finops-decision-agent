"""Oracle Cloud (OCI) pricing gate: single-shot fetch from the public price list API."""

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
    is_fresh,
    iso_compact,
    iso_z,
    latest_receipt_path,
    now_utc,
    sha256_bytes,
    store_root,
)

PROVIDER = "oracle"
SERVICE = "all-products"
PRICELIST_URL = "https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/"
FRESHNESS = timedelta(hours=24)

# Oracle publishes one global list price per SKU. The endpoint returns the full
# catalog (~640 items, ~3 MB) in a single response with no pagination and no auth.
# Compute - Virtual Machine, Compute - Bare Metal, and Compute - GPU SKUs live
# here alongside everything else. Modern shapes (E3-E5, X9, A1) price OCPU and
# memory separately, so a "4 vCPU 8 GB" answer combines two SKUs.
#
# Regional pricing variations exist for some Oracle services but are not exposed
# by this endpoint. The receipt records regions_included as ["global"] to make
# this explicit downstream.

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
    item_count: int
    upstream_last_updated: str | None
    files: list[FileRecord]
    total_size_bytes: int


async def fetch_pricelist() -> dict:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.get(PRICELIST_URL, timeout=120.0)
        resp.raise_for_status()
        return resp.json()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Oracle Cloud pricing snapshot.")
    parser.add_argument("--force", action="store_true", help="Bypass the 24h freshness rule.")
    args = parser.parse_args()

    if not args.force:
        latest = latest_receipt_path(PROVIDER)
        if latest is not None and is_fresh(latest, FRESHNESS):
            emit(json.loads(latest.read_text()))

    fetched_at = now_utc()
    snapshot_dir = STORE_ROOT / iso_compact(fetched_at)

    try:
        upstream = asyncio.run(fetch_pricelist())
    except httpx.HTTPError as exc:
        emit(
            {
                "success": False,
                "provider": PROVIDER,
                "error": str(exc),
                "hint": "Check network connectivity and that the Oracle price list endpoint is reachable.",
            },
            code=1,
        )

    items = upstream.get("items", [])
    upstream_last_updated = upstream.get("lastUpdated")

    snapshot_payload = {
        "fetched_at": iso_z(fetched_at),
        "source_url": PRICELIST_URL,
        "upstream_last_updated": upstream_last_updated,
        "item_count": len(items),
        "items": items,
    }
    snapshot_bytes = json.dumps(snapshot_payload, indent=2).encode("utf-8")

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "products.json").write_bytes(snapshot_bytes)

    files = [
        FileRecord(
            name="products.json",
            hash=sha256_bytes(snapshot_bytes),
            size_bytes=len(snapshot_bytes),
            source_url=PRICELIST_URL,
        )
    ]

    receipt = Receipt(
        success=True,
        provider=PROVIDER,
        service=SERVICE,
        source_url=PRICELIST_URL,
        store_dir=str(snapshot_dir.relative_to(PROJECT_ROOT)),
        fetched_at=iso_z(fetched_at),
        regions_included=["global"],
        item_count=len(items),
        upstream_last_updated=upstream_last_updated,
        files=files,
        total_size_bytes=sum(f.size_bytes for f in files),
    )

    (snapshot_dir / "receipt.json").write_text(json.dumps(asdict(receipt), indent=2))
    emit(asdict(receipt))


if __name__ == "__main__":
    main()
