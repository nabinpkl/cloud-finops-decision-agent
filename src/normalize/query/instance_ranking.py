"""Closest-larger filtering and ranking for instance-priced rows."""

from __future__ import annotations

from typing import Any

import polars as pl

from normalize.query.models import CandidateBrief


def filter_instance_rows(
    *,
    df: pl.DataFrame,
    region: str,
    family: str,
    vcpu: int,
    ram_gb: float,
    provider: str,
    any_family: str,
) -> pl.DataFrame:
    """Apply the closest-larger filter for one provider's instance rows."""
    region_filter = (pl.col("region_canonical") == region) | (pl.col("region_native") == region)
    out = df.filter(
        (pl.col("provider") == provider)
        & (pl.col("row_kind") == "instance")
        & region_filter
        & (pl.col("vcpu").is_not_null())
        & (pl.col("ram_gb").is_not_null())
        & (pl.col("vcpu") >= vcpu)
        & (pl.col("ram_gb") >= ram_gb)
    )
    if family != any_family:
        out = out.filter(pl.col("family") == family)
    return out


def rank_instance_rows(candidates: pl.DataFrame) -> pl.DataFrame:
    return candidates.sort(
        ["vcpu", "ram_gb", "monthly_usd", "hourly_usd"],
        descending=[False, False, False, False],
        nulls_last=True,
    )


def candidate_brief(row: dict[str, Any]) -> CandidateBrief:
    return CandidateBrief(
        instance_type=str(row["instance_type"]),
        vcpu=int(row["vcpu"]) if row.get("vcpu") is not None else None,
        ram_gb=float(row["ram_gb"]) if row.get("ram_gb") is not None else None,
        region_native=str(row["region_native"]),
        hourly_usd=row.get("hourly_usd"),
        monthly_usd=row.get("monthly_usd"),
    )
