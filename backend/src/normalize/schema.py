"""Parquet row schema, index_report schema, and drift flag enum.

Per ADR 0001 the parquet is the query target; per ADR 0003 the citation columns
are populated by the indexer with stable-ID JSONPath. Per ADR 0004 the flag set
is small and stable so eval can assert on it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, model_validator
from polars.datatypes import DataTypeClass


class SchemaModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# One row per priced SKU. The columns are the citation contract plus the
# comparable fields. Schema changes here ripple through every builder.
#
# Per ADR 0007 the schema discriminates row_kind:
#   - "instance" rows (the 5 atomic v0 providers): hourly_usd/monthly_usd are
#     per-instance costs; vcpu and ram_gb describe the priced shape.
#   - "rate" rows (GCP, Oracle): hourly_usd is a per-unit rate (per vCPU/hour or
#     per GB-RAM/hour). vcpu and ram_gb are null. rate_unit names the unit.
# Readers MUST branch on row_kind before aggregating prices.
#
# `pricing` says which market the price is for: "on_demand" (pay as you go) or
# "spot" (GCP Spot and Azure Spot capacity, reclaimable at any time). Readers
# MUST filter on it; a spot price is never an answer to an on-demand question.
#
# Prices are stored in the currency the provider published them in, never
# converted (ADR 0019): `hourly_usd`/`monthly_usd` for USD sources,
# `hourly_eur`/`monthly_eur` for sources that publish only EUR (Scaleway, OVHcloud
# Europe). A row carries one currency. Readers comparing prices MUST stay within
# one currency.
#
# `gpu_model`, `gpu_count` and `gpu_memory_gb` describe the accelerators a type
# includes, as the provider names them; null when the type has none or the source
# does not say. A GPU-priced type may have no vCPU or RAM figure (RunPod prices
# the GPU).
#
# `cited_price_scale` turns the number at `json_path` into the cited column:
# column = value_at_json_path * cited_price_scale. It is 1 for most sources;
# OVHcloud publishes integers in 1e-8 of a currency unit (1e-8), and Scaleway
# prices some GPU types per minute (60).
INDEX_SCHEMA: dict[str, DataTypeClass] = {
    "provider":             pl.String,
    "snapshot_iso":         pl.String,
    "instance_type":        pl.String,
    "family":               pl.String,        # taxonomy match; "unclassified" if no prefix matched
    "region_native":        pl.String,
    "region_canonical":     pl.String,        # nullable; null when outside the 3 v0 buckets
    "row_kind":             pl.String,        # "instance" or "rate" (ADR 0007)
    "pricing":              pl.String,        # "on_demand" | "spot"
    "rate_unit":            pl.String,        # null for instance rows; "per_vcpu_hour"|"per_gb_ram_hour"|"per_ocpu_hour" for rate rows
    "vcpu":                 pl.Int32,         # null for rate rows
    "ram_gb":               pl.Float64,       # null for rate rows
    "hourly_usd":           pl.Float64,       # per-instance for "instance" rows; per-unit for "rate" rows
    "monthly_usd":          pl.Float64,       # per-instance for "instance" rows; null for "rate" rows (rates are inherently hourly)
    "hourly_eur":           pl.Float64,       # set instead of hourly_usd when the source publishes only EUR
    "monthly_eur":          pl.Float64,
    "gpu_model":            pl.String,        # null when the type has no GPU or the source does not name it
    "gpu_count":            pl.Int32,
    "gpu_memory_gb":        pl.Float64,       # per GPU
    "source_url":           pl.String,
    "store_path":           pl.String,
    "json_path":            pl.String,        # JSONPath into store_path resolving to the price node
    "cited_price_kind":     pl.String,        # "hourly" | "monthly" | "rate_hourly" | "hourly_eur" | "monthly_eur"; the column the json_path leaf gives
    "cited_price_scale":    pl.Float64,       # column = json_path value * scale; 1 unless the source uses another unit
}

# The column each cited_price_kind names.
CITED_COLUMN: dict[str, str] = {
    "hourly": "hourly_usd",
    "monthly": "monthly_usd",
    "rate_hourly": "hourly_usd",
    "hourly_eur": "hourly_eur",
    "monthly_eur": "monthly_eur",
}


class IndexRow(SchemaModel):
    """A single parquet row. Builders return lists of these.

    `row_kind` defaults to "instance" so the 5 v0 atomic-pricing builders do not
    have to pass it explicitly. Rate-row builders (GCP, Oracle) override.
    """

    provider: str
    snapshot_iso: str
    instance_type: str
    family: str
    region_native: str
    region_canonical: str | None
    hourly_usd: float | None
    monthly_usd: float | None
    source_url: str
    store_path: str
    json_path: str
    cited_price_kind: Literal["hourly", "monthly", "rate_hourly", "hourly_eur", "monthly_eur"]
    vcpu: int | None = None
    ram_gb: float | None = None
    row_kind: Literal["instance", "rate"] = "instance"
    pricing: Literal["on_demand", "spot"] = "on_demand"
    rate_unit: Literal["per_vcpu_hour", "per_ocpu_hour", "per_gb_ram_hour"] | None = None
    hourly_eur: float | None = None
    monthly_eur: float | None = None
    gpu_model: str | None = None
    gpu_count: int | None = Field(default=None, ge=1)
    gpu_memory_gb: float | None = Field(default=None, gt=0)
    cited_price_scale: float = Field(default=1.0, gt=0)

    @model_validator(mode="after")
    def _one_currency_and_a_cited_price(self) -> IndexRow:
        has_usd = self.hourly_usd is not None or self.monthly_usd is not None
        has_eur = self.hourly_eur is not None or self.monthly_eur is not None
        if has_usd and has_eur:
            raise ValueError(f"{self.provider} {self.instance_type}: a row carries one currency, not USD and EUR")
        if getattr(self, CITED_COLUMN[self.cited_price_kind]) is None:
            raise ValueError(
                f"{self.provider} {self.instance_type}: cites {self.cited_price_kind} "
                f"but {CITED_COLUMN[self.cited_price_kind]} is empty"
            )
        return self

    def as_record(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class DriftFlag(StrEnum):
    """The complete set of flags the indexer may emit. Per ADR 0004."""

    SCHEMA_DRIFT                   = "schema_drift"
    NEW_UNCLASSIFIED_SHAPES        = "new_unclassified_shapes"
    FAMILY_COVERAGE_GAP            = "family_coverage_gap"
    ROW_COUNT_DROP                 = "row_count_drop"
    PRICE_SHIFT_DETECTED           = "price_shift_detected"
    CITATION_VERIFICATION_PARTIAL  = "citation_verification_partial"
    SNAPSHOT_STALE                 = "snapshot_stale"
    INDEX_REBUILD_FAILED_FELL_BACK = "index_rebuild_failed_fell_back"
    PROVIDER_UNAVAILABLE           = "provider_unavailable"
    FIELDS_MISSING                 = "fields_missing"
    NO_CANDIDATES                  = "no_candidates"


class CitationVerification(SchemaModel):
    sampled: int = 0
    passed: int = 0
    failed: int = 0
    failures: list[dict[str, Any]] = Field(default_factory=list)


class IndexReport(SchemaModel):
    """One per (provider, snapshot). Written as index_report.json next to the parquet."""

    provider: str
    snapshot_iso: str
    rows_written: int
    candidates: int
    missing_fields: dict[str, int]
    missing_field_pct: float | None
    accepted: bool
    rows_by_family: dict[str, int]
    unclassified_count: int
    unclassified_samples: list[dict[str, Any]]
    families_with_zero_coverage: list[str]
    row_count_delta_pct: float | None
    rows_in_previous_index: int | None
    citation_verification: CitationVerification
    flags: list[str]
    human_summary: str

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
