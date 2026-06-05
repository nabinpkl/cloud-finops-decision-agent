"""Synthesize resource-priced flex results from rate rows."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import polars as pl

from gates._shared import PROJECT_ROOT
from normalize.query_models import (
    CompareResult,
    CompositeCitation,
    CompositeCitationEntry,
)
from normalize.snapshot_time import snapshot_age_hours

FLEX_RULES_PATH: Path = PROJECT_ROOT / "normalize" / "taxonomy" / "flex_rules.json"
GLOBAL_RATE_PROVIDERS = {"oracle"}


def synthesize_rate_results(
    *,
    df: pl.DataFrame,
    provider: str,
    region: str,
    family: str,
    vcpu: int,
    ram_gb: float,
    receipt: dict[str, Any],
    any_family: str,
    hours_per_month: float,
) -> list[CompareResult]:
    """Compose custom-shape results from per-resource rate rows."""
    rules = load_flex_rules().get(provider, {})
    if not rules:
        return []

    rate_rows = df.filter((pl.col("provider") == provider) & (pl.col("row_kind") == "rate"))
    if rate_rows.is_empty():
        return []

    if provider not in GLOBAL_RATE_PROVIDERS:
        rate_rows = rate_rows.filter(
            (pl.col("region_canonical") == region) | (pl.col("region_native") == region)
        )

    if family != any_family:
        rate_rows = rate_rows.filter(pl.col("family") == family)

    if rate_rows.is_empty():
        return []

    rate_rows = rate_rows.with_columns(
        pl.col("instance_type").str.split(".").list.first().alias("_flex_family"),
    )

    results: list[CompareResult] = []
    fetched_at = str(receipt.get("fetched_at", ""))
    age_hours = snapshot_age_hours(fetched_at)

    for (flex_family, region_native), group in rate_rows.group_by(
        ["_flex_family", "region_native"], maintain_order=True
    ):
        rule = rules.get(flex_family)
        if rule is None:
            continue
        if not validate_ask(rule, vcpu, ram_gb):
            continue

        compute_row = first_rate(group, ("per_vcpu_hour", "per_ocpu_hour"))
        ram_row = first_rate(group, ("per_gb_ram_hour",))
        if compute_row is None or ram_row is None:
            continue

        compute_quantity = vcpu / float(rule["vcpu_per_unit"])
        compute_rate = float(compute_row["hourly_usd"])
        ram_rate = float(ram_row["hourly_usd"])
        compute_contribution = compute_quantity * compute_rate
        ram_contribution = ram_gb * ram_rate
        hourly = compute_contribution + ram_contribution
        if hourly <= 0:
            continue
        monthly = hourly * hours_per_month

        name = rule["custom_name_template"].format(
            vcpu=vcpu,
            ocpu=int(compute_quantity) if compute_quantity == int(compute_quantity) else compute_quantity,
            ram_gb=int(ram_gb) if ram_gb == int(ram_gb) else ram_gb,
            ram_mb=int(ram_gb * 1024),
        )

        citation = CompositeCitation(
            composite=[
                CompositeCitationEntry(
                    kind="rate",
                    rate_unit=compute_row["rate_unit"],
                    rate=compute_rate,
                    quantity=compute_quantity,
                    contribution_usd=compute_contribution,
                    source_url=compute_row["source_url"],
                    store_path=compute_row["store_path"],
                    json_path=compute_row["json_path"],
                    fetched_at=fetched_at,
                    age_hours=age_hours,
                ),
                CompositeCitationEntry(
                    kind="rate",
                    rate_unit=ram_row["rate_unit"],
                    rate=ram_rate,
                    quantity=ram_gb,
                    contribution_usd=ram_contribution,
                    source_url=ram_row["source_url"],
                    store_path=ram_row["store_path"],
                    json_path=ram_row["json_path"],
                    fetched_at=fetched_at,
                    age_hours=age_hours,
                ),
            ],
            synthesis={
                "rule": f"flex_rules.{provider}.{flex_family}",
                "formula": "vcpu_quantity * compute_rate + ram_gb * ram_rate",
            },
        )

        results.append(
            CompareResult(
                provider=provider,
                instance_type=name,
                region_native=str(region_native),
                vcpu_actual=vcpu,
                ram_gb_actual=ram_gb,
                hourly_usd=hourly,
                monthly_usd=monthly,
                considered_count=1,
                citation=citation,
                synthesized=True,
            )
        )

    for result in results:
        result.considered_count = len(results)
    return results


def first_rate(group: pl.DataFrame, units: tuple[str, ...]) -> dict[str, Any] | None:
    sub = group.filter(pl.col("rate_unit").is_in(list(units)))
    if sub.is_empty():
        return None
    return sub.row(0, named=True)


def validate_ask(rule: dict[str, Any], vcpu: int, ram_gb: float) -> bool:
    if vcpu < rule["vcpu_min"] or vcpu > rule["vcpu_max"]:
        return False
    step = int(rule.get("vcpu_step", 1))
    if step > 1:
        if vcpu != rule["vcpu_min"] and (vcpu - rule["vcpu_min"]) % step != 0:
            return False
    units = vcpu / float(rule["vcpu_per_unit"])
    if units <= 0:
        return False
    ratio = ram_gb / units
    ram_min = rule["ram_per_unit_gb"]["min"]
    ram_max = rule["ram_per_unit_gb"]["max"]
    if ratio < ram_min or ratio > ram_max:
        return False
    return True


@lru_cache(maxsize=1)
def load_flex_rules() -> dict[str, Any]:
    doc = json.loads(FLEX_RULES_PATH.read_text())
    return {k: v for k, v in doc.items() if not k.startswith("_")}
