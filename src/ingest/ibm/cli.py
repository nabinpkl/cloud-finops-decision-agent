"""IBM ingest CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict

import httpx

from ingest._shared import emit, is_fresh, iso_compact, latest_receipt_path, now_utc
from ingest.ibm.catalog import fetch_all
from ingest.ibm.constants import FRESHNESS, PROVIDER, STORE_ROOT
from ingest.ibm.snapshot import build_receipt, write_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch IBM Cloud catalog and compute pricing.")
    parser.add_argument("--force", action="store_true", help="Bypass the 24h freshness rule.")
    args = parser.parse_args()

    if not args.force:
        latest = latest_receipt_path(PROVIDER)
        if latest is not None and is_fresh(latest, FRESHNESS):
            emit(json.loads(latest.read_text()))

    fetched_at = now_utc()
    snapshot_dir = STORE_ROOT / iso_compact(fetched_at)

    try:
        services, page_count, compute, pricing_calls, regions = asyncio.run(fetch_all())
    except httpx.HTTPError as exc:
        emit(
            {
                "success": False,
                "provider": PROVIDER,
                "error": str(exc),
                "hint": "Check network connectivity and that globalcatalog.cloud.ibm.com is reachable.",
            },
            code=1,
        )

    written = write_snapshot(
        snapshot_dir=snapshot_dir,
        fetched_at=fetched_at,
        services=services,
        page_count=page_count,
        compute=compute,
    )
    receipt = build_receipt(
        snapshot_dir=snapshot_dir,
        fetched_at=fetched_at,
        regions=regions,
        service_count=len(services),
        page_count=page_count,
        plan_count=written.plan_count,
        pricing_calls=pricing_calls,
        files=written.files,
    )

    (snapshot_dir / "receipt.json").write_text(json.dumps(asdict(receipt), indent=2))
    emit(asdict(receipt))
