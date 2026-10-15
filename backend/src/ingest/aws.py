"""AWS EC2 pricing ingest: the bulk Price List files for the v0 regions, stored raw."""

from __future__ import annotations

import asyncio

import httpx

from ingest._shared import IngestError
from ingest.config import ingest_settings
from ingest.raw_snapshot import RawSnapshot, run_ingest

PROVIDER = "aws"
SERVICE = "ec2"
PRICING_HOST = "https://pricing.us-east-1.amazonaws.com"
REGION_INDEX_PATH = "/offers/v1.0/aws/AmazonEC2/current/region_index.json"
REGION_INDEX_URL = PRICING_HOST + REGION_INDEX_PATH
REGION_INDEX_FILE = "region_index.json"
REGIONS: list[str] = ["us-east-1", "eu-central-1", "ap-southeast-1"]

# Each region file is the full bulk offer for EC2 in that region (~480 MB in Oct
# 2026). Bodies stream to disk; nothing holds a region file in memory here.


def region_file(region: str) -> str:
    return f"{region}.json"


async def fetch(snapshot: RawSnapshot, client: httpx.AsyncClient) -> None:
    await snapshot.fetch(
        client,
        REGION_INDEX_FILE,
        REGION_INDEX_URL,
        timeout=ingest_settings.http_short_timeout_seconds,
    )
    offers = snapshot.read_json(REGION_INDEX_FILE).get("regions")
    if not isinstance(offers, dict):
        raise IngestError(f"{REGION_INDEX_FILE}: no `regions` object; the index shape changed")
    missing = [r for r in REGIONS if r not in offers]
    if missing:
        raise IngestError(f"AWS regions not in {REGION_INDEX_FILE}: {missing}")

    await asyncio.gather(
        *(
            snapshot.fetch(
                client,
                region_file(region),
                PRICING_HOST + offers[region]["currentVersionUrl"],
                timeout=ingest_settings.http_large_timeout_seconds,
            )
            for region in REGIONS
        )
    )


def main() -> None:
    run_ingest(
        provider=PROVIDER,
        service=SERVICE,
        source_url=REGION_INDEX_URL,
        fetch=fetch,
        hint="Check network connectivity and that REGIONS matches AWS region_index.json.",
        description="Fetch AWS EC2 pricing snapshot.",
    )


if __name__ == "__main__":
    main()
