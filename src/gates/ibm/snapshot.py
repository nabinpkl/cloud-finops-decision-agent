"""IBM snapshot payload and receipt writing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from gates._shared import FileRecord, PROJECT_ROOT, iso_z, sha256_bytes
from gates.ibm.constants import CATALOG_BASE, CATALOG_FIRST_URL, COMPUTE_SERVICE_NAMES, PROVIDER, SERVICE


@dataclass
class Receipt:
    success: bool
    provider: str
    service: str
    source_url: str
    store_dir: str
    fetched_at: str
    regions_included: list[str]
    compute_services_included: list[str]
    service_count: int
    catalog_page_count: int
    plan_count: int
    pricing_call_count: int
    files: list[FileRecord]
    total_size_bytes: int


@dataclass
class SnapshotWrite:
    files: list[FileRecord]
    plan_count: int


def write_snapshot(
    *,
    snapshot_dir: Path,
    fetched_at: datetime,
    services: list[dict],
    page_count: int,
    compute: dict[str, dict],
) -> SnapshotWrite:
    services_bytes = json.dumps(
        {
            "fetched_at": iso_z(fetched_at),
            "source_url": CATALOG_FIRST_URL,
            "page_count": page_count,
            "service_count": len(services),
            "services": services,
        },
        indent=2,
    ).encode("utf-8")

    compute_bytes = json.dumps(
        {
            "fetched_at": iso_z(fetched_at),
            "compute_services_included": COMPUTE_SERVICE_NAMES,
            "compute": compute,
        },
        indent=2,
    ).encode("utf-8")

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "services.json").write_bytes(services_bytes)
    (snapshot_dir / "compute.json").write_bytes(compute_bytes)

    files = [
        FileRecord(
            name="services.json",
            hash=sha256_bytes(services_bytes),
            size_bytes=len(services_bytes),
            source_url=CATALOG_FIRST_URL,
        ),
        FileRecord(
            name="compute.json",
            hash=sha256_bytes(compute_bytes),
            size_bytes=len(compute_bytes),
            source_url=f"{CATALOG_BASE}/<plan-id>/pricing/deployment",
        ),
    ]
    plan_count = sum(len(entry.get("plans", [])) for entry in compute.values())
    return SnapshotWrite(files=files, plan_count=plan_count)


def build_receipt(
    *,
    snapshot_dir: Path,
    fetched_at: datetime,
    regions: set[str],
    service_count: int,
    page_count: int,
    plan_count: int,
    pricing_calls: int,
    files: list[FileRecord],
) -> Receipt:
    return Receipt(
        success=True,
        provider=PROVIDER,
        service=SERVICE,
        source_url=CATALOG_FIRST_URL,
        store_dir=str(snapshot_dir.relative_to(PROJECT_ROOT)),
        fetched_at=iso_z(fetched_at),
        regions_included=sorted(regions),
        compute_services_included=COMPUTE_SERVICE_NAMES,
        service_count=service_count,
        catalog_page_count=page_count,
        plan_count=plan_count,
        pricing_call_count=pricing_calls,
        files=files,
        total_size_bytes=sum(file.size_bytes for file in files),
    )

