"""Parquet row schema, index_report schema, and drift flag enum.

Per ADR 0001 the parquet is the query target; per ADR 0003 the citation columns
are populated by the indexer with stable-ID JSONPath. Per ADR 0004 the flag set
is small and stable so eval can assert on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import polars as pl


# One row per priced SKU. The columns are the citation contract plus the
# comparable fields. Schema changes here ripple through every builder.
#
# Per ADR 0007 the schema discriminates row_kind:
#   - "instance" rows (the 5 atomic v0 providers): hourly_usd/monthly_usd are
#     per-instance costs; vcpu and ram_gb describe the priced shape.
#   - "rate" rows (GCP, Oracle): hourly_usd is a per-unit rate (per vCPU/hour or
#     per GB-RAM/hour). vcpu and ram_gb are null. rate_unit names the unit.
# Readers MUST branch on row_kind before aggregating prices.
INDEX_SCHEMA: dict[str, pl.DataType] = {
    "provider":             pl.String,
    "snapshot_iso":         pl.String,
    "instance_type":        pl.String,
    "family":               pl.String,        # taxonomy match; "unclassified" if no prefix matched
    "region_native":        pl.String,
    "region_canonical":     pl.String,        # nullable; null when outside the 3 v0 buckets
    "row_kind":             pl.String,        # "instance" or "rate" (ADR 0007)
    "rate_unit":            pl.String,        # null for instance rows; "per_vcpu_hour"|"per_gb_ram_hour"|"per_ocpu_hour" for rate rows
    "vcpu":                 pl.Int32,         # null for rate rows
    "ram_gb":               pl.Float64,       # null for rate rows
    "hourly_usd":           pl.Float64,       # per-instance for "instance" rows; per-unit for "rate" rows
    "monthly_usd":          pl.Float64,       # per-instance for "instance" rows; null for "rate" rows (rates are inherently hourly)
    "source_url":           pl.String,
    "store_path":           pl.String,
    "json_path":            pl.String,        # JSONPath into store_path resolving to the price node
    "cited_price_kind":     pl.String,        # "hourly" | "monthly" | "rate_hourly"; tells the verifier which column the json_path leaf equals
}


@dataclass
class IndexRow:
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
    cited_price_kind: str  # "hourly" | "monthly" | "rate_hourly"
    vcpu: int | None = None
    ram_gb: float | None = None
    row_kind: str = "instance"
    rate_unit: str | None = None

    def as_record(self) -> dict[str, Any]:
        return {
            "provider":         self.provider,
            "snapshot_iso":     self.snapshot_iso,
            "instance_type":    self.instance_type,
            "family":           self.family,
            "region_native":    self.region_native,
            "region_canonical": self.region_canonical,
            "row_kind":         self.row_kind,
            "rate_unit":        self.rate_unit,
            "vcpu":             self.vcpu,
            "ram_gb":           self.ram_gb,
            "hourly_usd":       self.hourly_usd,
            "monthly_usd":      self.monthly_usd,
            "source_url":       self.source_url,
            "store_path":       self.store_path,
            "json_path":        self.json_path,
            "cited_price_kind": self.cited_price_kind,
        }


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


@dataclass
class CitationVerification:
    sampled: int = 0
    passed: int = 0
    failed: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class IndexReport:
    """One per (provider, snapshot). Written as index_report.json next to the parquet."""

    provider: str
    snapshot_iso: str
    rows_written: int
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
        return {
            "provider":                    self.provider,
            "snapshot_iso":                self.snapshot_iso,
            "rows_written":                self.rows_written,
            "rows_by_family":              self.rows_by_family,
            "unclassified_count":          self.unclassified_count,
            "unclassified_samples":        self.unclassified_samples,
            "families_with_zero_coverage": self.families_with_zero_coverage,
            "row_count_delta_pct":         self.row_count_delta_pct,
            "rows_in_previous_index":      self.rows_in_previous_index,
            "citation_verification": {
                "sampled":  self.citation_verification.sampled,
                "passed":   self.citation_verification.passed,
                "failed":   self.citation_verification.failed,
                "failures": self.citation_verification.failures,
            },
            "flags":                       self.flags,
            "human_summary":               self.human_summary,
        }
