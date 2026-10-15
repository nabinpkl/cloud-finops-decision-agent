"""Shared test data builders."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections.abc import Mapping
from typing import Literal

import polars as pl

from normalize.schema import INDEX_SCHEMA, IndexRow


def make_df(rows: list[IndexRow]) -> pl.DataFrame:
    """Build a parquet-faithful DataFrame from IndexRow objects."""
    records = [row.as_record() for row in rows]
    return pl.DataFrame(records, schema=INDEX_SCHEMA, orient="row")


def write_receipt(snapshot_dir: Path, *, fetched_at: str) -> None:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "receipt.json").write_text(json.dumps({"fetched_at": fetched_at}))


def iso_hours_ago(hours: float) -> str:
    """An ISO-8601 UTC timestamp with trailing Z, `hours` in the past."""
    ts = datetime.now(timezone.utc) - timedelta(hours=hours)
    return ts.isoformat().replace("+00:00", "Z")


def make_snapshot(
    store: Path,
    provider: str,
    iso: str,
    *,
    fetched_at: str,
    report: dict,
) -> Path:
    """Create a minimal on-disk snapshot that data_quality can read."""
    snapshot_dir = store / provider / iso
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "index.parquet").write_bytes(b"")
    (snapshot_dir / "receipt.json").write_text(json.dumps({"fetched_at": fetched_at}))
    (snapshot_dir / "index_report.json").write_text(json.dumps(report))
    return snapshot_dir


def instance_row(
    *,
    provider: str,
    instance_type: str,
    family: str,
    region_canonical: str,
    vcpu: int,
    ram_gb: float,
    hourly_usd: float,
    monthly_usd: float,
    region_native: str | None = None,
) -> IndexRow:
    return IndexRow(
        provider=provider,
        snapshot_iso="2026-05-27T00-00-00Z",
        instance_type=instance_type,
        family=family,
        region_native=region_native or region_canonical,
        region_canonical=region_canonical,
        hourly_usd=hourly_usd,
        monthly_usd=monthly_usd,
        source_url=f"https://example.test/{provider}",
        store_path=f"store/{provider}/2026-05-27T00-00-00Z/data.json",
        json_path=f"$.{instance_type}.price",
        cited_price_kind="monthly",
        vcpu=vcpu,
        ram_gb=ram_gb,
        row_kind="instance",
    )


def rate_row(
    *,
    provider: str,
    flex_family: str,
    resource: str,
    family: str,
    region_canonical: str | None,
    rate_unit: Literal["per_vcpu_hour", "per_ocpu_hour", "per_gb_ram_hour"],
    hourly_usd: float,
    region_native: str | None = None,
) -> IndexRow:
    return IndexRow(
        provider=provider,
        snapshot_iso="2026-05-27T00-00-00Z",
        instance_type=f"{flex_family}.{resource}",
        family=family,
        region_native=region_native or (region_canonical or "global"),
        region_canonical=region_canonical,
        hourly_usd=hourly_usd,
        monthly_usd=None,
        source_url=f"https://example.test/{provider}",
        store_path=f"store/{provider}/2026-05-27T00-00-00Z/data.json",
        json_path=f"$.{flex_family}.{resource}.rate",
        cited_price_kind="rate_hourly",
        vcpu=None,
        ram_gb=None,
        row_kind="rate",
        rate_unit=rate_unit,
    )


def write_raw_snapshot(store: Path, provider: str, iso: str, files: Mapping[str, object]) -> Path:
    """Write a raw-first snapshot the way ingest leaves one: each response body as its
    own file plus a receipt (version 2) listing them."""
    snapshot_dir = store / provider / iso
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for name, body in files.items():
        data = json.dumps(body).encode()
        (snapshot_dir / name).write_bytes(data)
        entries.append(
            {
                "name": name,
                "url": f"https://api.example.test/{provider}/{name}",
                "status": 200,
                "headers": {},
                "sha256": "",
                "size_bytes": len(data),
                "fetched_at": iso_hours_ago(1),
            }
        )
    (snapshot_dir / "receipt.json").write_text(
        json.dumps(
            {
                "receipt_version": 2,
                "success": True,
                "provider": provider,
                "service": "test",
                "source_url": f"https://api.example.test/{provider}",
                "store_dir": f"store/{provider}/{iso}",
                "fetched_at": iso_hours_ago(1),
                "completed_at": iso_hours_ago(1),
                "resumed": False,
                "files": entries,
                "total_size_bytes": sum(e["size_bytes"] for e in entries),
            }
        )
    )
    return snapshot_dir
