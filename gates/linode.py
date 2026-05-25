"""Linode (Akamai) pricing gate: single-shot fetch from the public /v4/linode/types endpoint."""

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

PROVIDER = "linode"
SERVICE = "types"
TYPES_URL = "https://api.linode.com/v4/linode/types"
FRESHNESS = timedelta(hours=24)

# Linode's /v4/linode/types is no-auth, single page (~75 types, ~42 KB).
# The schema is distinctive: each type carries a base price.{hourly,monthly} that
# applies globally, PLUS an explicit region_prices[] list of per-region overrides
# (id, hourly, monthly) for regions that price differently from the base. No
# other provider in v0 publishes per-region overrides like this. The agent must
# read region_prices[] before quoting a region-specific number; the base price is
# the fallback for any region not listed.

STORE_ROOT = store_root(PROVIDER)


@dataclass
class Receipt:
    success: bool
    provider: str
    service: str
    source_url: str
    store_dir: str
    fetched_at: str
    regions_with_overrides: list[str]
    type_count: int
    files: list[FileRecord]
    total_size_bytes: int


async def fetch_types() -> dict:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.get(TYPES_URL, timeout=120.0)
        resp.raise_for_status()
        return resp.json()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Linode pricing snapshot.")
    parser.add_argument("--force", action="store_true", help="Bypass the 24h freshness rule.")
    args = parser.parse_args()

    if not args.force:
        latest = latest_receipt_path(PROVIDER)
        if latest is not None and is_fresh(latest, FRESHNESS):
            emit(json.loads(latest.read_text()))

    fetched_at = now_utc()
    snapshot_dir = STORE_ROOT / iso_compact(fetched_at)

    try:
        upstream = asyncio.run(fetch_types())
    except httpx.HTTPError as exc:
        emit(
            {
                "success": False,
                "provider": PROVIDER,
                "error": str(exc),
                "hint": "Check network connectivity and that api.linode.com is reachable.",
            },
            code=1,
        )

    types_ = upstream.get("data", [])
    regions_with_overrides = sorted(
        {rp["id"] for t in types_ for rp in t.get("region_prices", []) if "id" in rp}
    )

    snapshot_payload = {
        "fetched_at": iso_z(fetched_at),
        "source_url": TYPES_URL,
        "type_count": len(types_),
        "types": types_,
    }
    snapshot_bytes = json.dumps(snapshot_payload, indent=2).encode("utf-8")

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "types.json").write_bytes(snapshot_bytes)

    files = [
        FileRecord(
            name="types.json",
            hash=sha256_bytes(snapshot_bytes),
            size_bytes=len(snapshot_bytes),
            source_url=TYPES_URL,
        )
    ]

    receipt = Receipt(
        success=True,
        provider=PROVIDER,
        service=SERVICE,
        source_url=TYPES_URL,
        store_dir=str(snapshot_dir.relative_to(PROJECT_ROOT)),
        fetched_at=iso_z(fetched_at),
        regions_with_overrides=regions_with_overrides,
        type_count=len(types_),
        files=files,
        total_size_bytes=sum(f.size_bytes for f in files),
    )

    (snapshot_dir / "receipt.json").write_text(json.dumps(asdict(receipt), indent=2))
    emit(asdict(receipt))


if __name__ == "__main__":
    main()
