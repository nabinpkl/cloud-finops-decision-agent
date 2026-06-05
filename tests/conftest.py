"""Shared test fixtures and helpers.

Two lanes (per the discussion in this session):
  - mocked integration: deterministic fixtures, no real store/ dependency.
    These build in-memory parquet frames or tiny on-disk stores and patch the
    loader / data_quality seams.
  - e2e real-file: run against whatever is in store/ (marked `e2e`, skipped
    when nothing is indexed).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

# Budget enforcement (ADR-0011) is off by default in tests; tests that need
# it on flip settings.budget_enabled at the instance level via monkeypatch.
# Setting the salt belt-and-braces in case a test enables budgets without
# providing one explicitly.
os.environ.setdefault("BUDGET_ENABLED", "false")
os.environ.setdefault(
    "BUDGET_IP_HASH_SALT_SECRET", "test-salt-not-a-real-secret-32-bytes"
)

import polars as pl  # noqa: E402

from normalize.schema import INDEX_SCHEMA, IndexRow  # noqa: E402


def make_df(rows: list[IndexRow]) -> pl.DataFrame:
    """Build a parquet-faithful DataFrame from IndexRow objects, exactly as
    normalize.index._write_parquet does."""
    records = [r.as_record() for r in rows]
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
    """Create a minimal on-disk snapshot under `store` that data_quality can
    read: an (empty) index.parquet for presence, a receipt.json, and an
    index_report.json. latest_snapshot_dir only checks parquet existence; the
    quality envelope reads the two JSON files."""
    d = store / provider / iso
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.parquet").write_bytes(b"")
    (d / "receipt.json").write_text(json.dumps({"fetched_at": fetched_at}))
    (d / "index_report.json").write_text(json.dumps(report))
    return d


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
    resource: str,  # "cpu" | "ram"
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
