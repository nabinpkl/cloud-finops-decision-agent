"""AWS EC2 pricing gate: fetch the v0 region set and snapshot to store/aws/<ISO>/."""

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

PROVIDER = "aws"
SERVICE = "ec2"
PRICING_HOST = "https://pricing.us-east-1.amazonaws.com"
REGION_INDEX_PATH = "/offers/v1.0/aws/AmazonEC2/current/region_index.json"
REGION_INDEX_URL = PRICING_HOST + REGION_INDEX_PATH
FRESHNESS = timedelta(hours=24)
REGIONS: list[str] = ["us-east-1", "eu-central-1", "ap-southeast-1"]

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
    files: list[FileRecord]
    total_size_bytes: int


async def fetch_bytes(client: httpx.AsyncClient, url: str, timeout: float) -> bytes:
    resp = await client.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.content


async def fetch_all(regions: list[str]) -> tuple[bytes, dict[str, tuple[bytes, str]]]:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        index_bytes = await fetch_bytes(client, REGION_INDEX_URL, timeout=60.0)
        index = json.loads(index_bytes)
        offers = index["regions"]
        missing = [r for r in regions if r not in offers]
        if missing:
            raise ValueError(f"AWS regions not in region_index.json: {missing}")

        async def one(region: str) -> tuple[str, bytes, str]:
            url = PRICING_HOST + offers[region]["currentVersionUrl"]
            data = await fetch_bytes(client, url, timeout=600.0)
            return region, data, url

        results = await asyncio.gather(*(one(r) for r in regions))
        return index_bytes, {r: (data, url) for r, data, url in results}


def write_snapshot(
    snapshot_dir,
    index_bytes: bytes,
    region_data: dict[str, tuple[bytes, str]],
) -> list[FileRecord]:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    files: list[FileRecord] = [
        FileRecord(
            name="region_index.json",
            hash=sha256_bytes(index_bytes),
            size_bytes=len(index_bytes),
            source_url=REGION_INDEX_URL,
        )
    ]
    (snapshot_dir / "region_index.json").write_bytes(index_bytes)

    for region, (data, url) in sorted(region_data.items()):
        (snapshot_dir / f"{region}.json").write_bytes(data)
        files.append(
            FileRecord(
                name=f"{region}.json",
                hash=sha256_bytes(data),
                size_bytes=len(data),
                source_url=url,
            )
        )

    return files


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch AWS EC2 pricing snapshot.")
    parser.add_argument("--force", action="store_true", help="Bypass the 24h freshness rule.")
    args = parser.parse_args()

    if not args.force:
        latest = latest_receipt_path(PROVIDER)
        if latest is not None and is_fresh(latest, FRESHNESS):
            emit(json.loads(latest.read_text()))

    fetched_at = now_utc()
    snapshot_dir = STORE_ROOT / iso_compact(fetched_at)

    try:
        index_bytes, region_data = asyncio.run(fetch_all(REGIONS))
    except (httpx.HTTPError, ValueError) as exc:
        emit(
            {
                "success": False,
                "provider": PROVIDER,
                "error": str(exc),
                "hint": "Check network connectivity and that REGIONS matches AWS region_index.json.",
            },
            code=1,
        )

    files = write_snapshot(snapshot_dir, index_bytes, region_data)
    receipt = Receipt(
        success=True,
        provider=PROVIDER,
        service=SERVICE,
        source_url=REGION_INDEX_URL,
        store_dir=str(snapshot_dir.relative_to(PROJECT_ROOT)),
        fetched_at=iso_z(fetched_at),
        regions_included=sorted(region_data),
        files=files,
        total_size_bytes=sum(f.size_bytes for f in files),
    )

    (snapshot_dir / "receipt.json").write_text(json.dumps(asdict(receipt), indent=2))
    emit(asdict(receipt))


if __name__ == "__main__":
    main()
