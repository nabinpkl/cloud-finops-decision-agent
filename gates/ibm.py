"""IBM Cloud pricing gate: paginated fetch from the public Global Catalog API."""

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
    is_fresh,
    iso_compact,
    iso_z,
    latest_receipt_path,
    now_utc,
    sha256_bytes,
    store_root,
)

PROVIDER = "ibm"
SERVICE = "global-catalog"
CATALOG_BASE = "https://globalcatalog.cloud.ibm.com/api/v1"
FIRST_URL = f"{CATALOG_BASE}?q=kind:service"
FRESHNESS = timedelta(hours=24)

# IBM's Global Catalog API is no-auth, paginated 50 entries per page via a `next`
# URL embedded in the response (top-level). ~319 services total => ~7 pages,
# ~6 MB combined. The catalog spans every IBM Cloud product (Watson, Cloud Paks,
# IaaS, PaaS, Kubernetes, databases). Compute SKUs live alongside everything else
# under `kind:service`; the agent filters by name/category for VPC Virtual
# Servers, Bare Metal Servers, etc.
#
# Pricing detail is NOT inlined on the catalog entry. Each service entry has a
# `children_url` or per-plan pricing endpoint the agent must follow if it needs
# the actual unit prices. v0 snapshots the catalog itself; pricing follow-up is
# a v1 question once we know which services the agent actually quotes.

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
    service_count: int
    page_count: int
    files: list[FileRecord]
    total_size_bytes: int


def log(msg: str) -> None:
    print(f"[ibm] {msg}", file=sys.stderr, flush=True)


async def fetch_all_services() -> tuple[list[dict], int]:
    services: list[dict] = []
    pages = 0
    next_url: str | None = FIRST_URL

    async with httpx.AsyncClient(follow_redirects=True) as client:
        while next_url:
            pages += 1
            t0 = now_utc()
            resp = await client.get(next_url, timeout=120.0)
            dt = (now_utc() - t0).total_seconds()
            resp.raise_for_status()
            page = resp.json()
            chunk = page.get("resources", [])
            services.extend(chunk)
            log(
                f"page {pages}: 200 in {dt:.2f}s "
                f"resources+={len(chunk)} total={len(services)} "
                f"(reported_count={page.get('count')})"
            )
            next_url = page.get("next") or None

    return services, pages


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch IBM Cloud Global Catalog snapshot.")
    parser.add_argument("--force", action="store_true", help="Bypass the 24h freshness rule.")
    args = parser.parse_args()

    if not args.force:
        latest = latest_receipt_path(PROVIDER)
        if latest is not None and is_fresh(latest, FRESHNESS):
            emit(json.loads(latest.read_text()))

    fetched_at = now_utc()
    snapshot_dir = STORE_ROOT / iso_compact(fetched_at)

    try:
        services, page_count = asyncio.run(fetch_all_services())
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

    snapshot_payload = {
        "fetched_at": iso_z(fetched_at),
        "source_url": FIRST_URL,
        "page_count": page_count,
        "service_count": len(services),
        "services": services,
    }
    snapshot_bytes = json.dumps(snapshot_payload, indent=2).encode("utf-8")

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "services.json").write_bytes(snapshot_bytes)

    files = [
        FileRecord(
            name="services.json",
            hash=sha256_bytes(snapshot_bytes),
            size_bytes=len(snapshot_bytes),
            source_url=FIRST_URL,
        )
    ]

    receipt = Receipt(
        success=True,
        provider=PROVIDER,
        service=SERVICE,
        source_url=FIRST_URL,
        store_dir=str(snapshot_dir.relative_to(PROJECT_ROOT)),
        fetched_at=iso_z(fetched_at),
        regions_included=[],
        service_count=len(services),
        page_count=page_count,
        files=files,
        total_size_bytes=sum(f.size_bytes for f in files),
    )

    (snapshot_dir / "receipt.json").write_text(json.dumps(asdict(receipt), indent=2))
    emit(asdict(receipt))


if __name__ == "__main__":
    main()
