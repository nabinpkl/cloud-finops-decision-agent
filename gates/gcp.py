"""GCP Compute Engine pricing gate: fetch all SKUs via the Cloud Billing Catalog API."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
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
    load_dotenv_if_present,
    now_utc,
    sha256_bytes,
    store_root,
)

PROVIDER = "gcp"
SERVICE = "compute"
COMPUTE_SERVICE_ID = "6F81-5844-456A"
CATALOG_BASE = f"https://cloudbilling.googleapis.com/v1/services/{COMPUTE_SERVICE_ID}/skus"
PAGE_SIZE = 5000  # API hard maximum; values above this return HTTP 400.
FRESHNESS = timedelta(hours=24)

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
    sku_count: int
    page_count: int
    files: list[FileRecord]
    total_size_bytes: int


async def fetch_all_skus(api_key: str) -> tuple[list[dict], int]:
    headers = {"X-goog-api-key": api_key}
    skus: list[dict] = []
    page_count = 0
    token: str | None = None

    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        while True:
            params: dict[str, str | int] = {"pageSize": PAGE_SIZE}
            if token:
                params["pageToken"] = token
            resp = await client.get(CATALOG_BASE, params=params, timeout=120.0)
            resp.raise_for_status()
            page = resp.json()
            page_count += 1
            skus.extend(page.get("skus", []))
            token = page.get("nextPageToken") or None
            if not token:
                break

    return skus, page_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch GCP Compute Engine pricing snapshot.")
    parser.add_argument("--force", action="store_true", help="Bypass the 24h freshness rule.")
    args = parser.parse_args()

    if not args.force:
        latest = latest_receipt_path(PROVIDER)
        if latest is not None and is_fresh(latest, FRESHNESS):
            emit(json.loads(latest.read_text()))

    load_dotenv_if_present()
    api_key = os.environ.get("GCP_API_KEY")
    if not api_key:
        emit(
            {
                "success": False,
                "provider": PROVIDER,
                "error": "GCP_API_KEY not set",
                "hint": "Provision a key with the Cloud Billing API enabled. Add GCP_API_KEY=... to .env at the project root. See .env.example.",
            },
            code=1,
        )

    fetched_at = now_utc()
    snapshot_dir = STORE_ROOT / iso_compact(fetched_at)

    try:
        skus, page_count = asyncio.run(fetch_all_skus(api_key))
    except httpx.HTTPError as exc:
        emit(
            {
                "success": False,
                "provider": PROVIDER,
                "error": str(exc),
                "hint": "Check GCP_API_KEY validity and Cloud Billing API quota.",
            },
            code=1,
        )

    regions = sorted({r for sku in skus for r in sku.get("serviceRegions", [])})

    snapshot_payload = {
        "fetched_at": iso_z(fetched_at),
        "service_id": COMPUTE_SERVICE_ID,
        "source_url": CATALOG_BASE,
        "page_count": page_count,
        "sku_count": len(skus),
        "skus": skus,
    }
    snapshot_bytes = json.dumps(snapshot_payload, indent=2).encode("utf-8")

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / "skus.json"
    snapshot_path.write_bytes(snapshot_bytes)

    files = [
        FileRecord(
            name="skus.json",
            hash=sha256_bytes(snapshot_bytes),
            size_bytes=len(snapshot_bytes),
            source_url=CATALOG_BASE,
        )
    ]

    receipt = Receipt(
        success=True,
        provider=PROVIDER,
        service=SERVICE,
        source_url=CATALOG_BASE,
        store_dir=str(snapshot_dir.relative_to(PROJECT_ROOT)),
        fetched_at=iso_z(fetched_at),
        regions_included=regions,
        sku_count=len(skus),
        page_count=page_count,
        files=files,
        total_size_bytes=sum(f.size_bytes for f in files),
    )

    (snapshot_dir / "receipt.json").write_text(json.dumps(asdict(receipt), indent=2))
    emit(asdict(receipt))


if __name__ == "__main__":
    main()
