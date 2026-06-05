"""Builds the data_quality envelope per ADR 0005.

Reads each provider's index_report.json and receipt.json, computes
per-provider status (ok | warn | stale | broken), surfaces flags and
human_summary, and rolls up to overall_status.

`age_hours` is computed in timezone-aware UTC per AGENTS.md's UTC-parsing
pitfall."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ingest.config import ingest_settings
from project_paths import PROJECT_ROOT
from normalize.query.loader import latest_snapshot_dir
from normalize.schema import DriftFlag
from normalize.snapshot_time import snapshot_age_hours

STALENESS_THRESHOLD_HOURS = ingest_settings.snapshot_freshness_hours

# Status ordering for the rollup: rightmost wins when comparing.
_STATUS_ORDER = ["ok", "warn", "stale", "broken"]


class ProviderQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "warn", "stale", "broken"]
    snapshot_age_hours: float | None
    flags: list[DriftFlag]
    human_summary: str
    report_path: str | None = None
    snapshot_iso: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = self.model_dump(mode="json")
        if self.report_path is None:
            out.pop("report_path", None)
        if self.snapshot_iso is None:
            out.pop("snapshot_iso", None)
        if not self.evidence:
            out.pop("evidence", None)
        return out


def compute_envelope(providers: list[str]) -> dict[str, Any]:
    """Return the data_quality envelope for a response that drew on `providers`.

    Each provider in the request gets one per_provider entry. A provider with
    no usable snapshot is recorded as status="broken" with the
    `provider_unavailable` flag so the agent's prose surfaces the exclusion.
    """
    per_provider: dict[str, dict[str, Any]] = {}
    for p in providers:
        per_provider[p] = _quality_for(p).to_dict()

    statuses = [pp["status"] for pp in per_provider.values()] or ["broken"]
    overall = max(statuses, key=_STATUS_ORDER.index)

    return {
        "overall_status": overall,
        "per_provider":   per_provider,
    }


def _quality_for(provider: str) -> ProviderQuality:
    snapshot_dir = latest_snapshot_dir(provider)
    if snapshot_dir is None:
        return ProviderQuality(
            status="broken",
            snapshot_age_hours=None,
            flags=[DriftFlag.PROVIDER_UNAVAILABLE],
            human_summary=f"No usable {provider} snapshot found; the provider was excluded from this response.",
        )

    receipt_path = snapshot_dir / "receipt.json"
    report_path = snapshot_dir / "index_report.json"

    age_hours = _age_hours_from_receipt(receipt_path)
    report = json.loads(report_path.read_text()) if report_path.exists() else {}
    flags = [DriftFlag(flag) for flag in report.get("flags", [])]
    human_summary = report.get("human_summary", f"{provider} index ready.")

    if age_hours is not None and age_hours > STALENESS_THRESHOLD_HOURS:
        if DriftFlag.SNAPSHOT_STALE not in flags:
            flags.append(DriftFlag.SNAPSHOT_STALE)

    status = _derive_status(flags=flags, age_hours=age_hours)
    if status == "stale":
        human_summary = (
            f"{provider} snapshot is {age_hours:.1f}h old, past the "
            f"{STALENESS_THRESHOLD_HOURS:g}h freshness threshold. "
            f"Re-fetch with `just fetch-force {provider}` for live prices."
        )

    return ProviderQuality(
        status=status,
        snapshot_age_hours=age_hours,
        flags=flags,
        human_summary=human_summary,
        report_path=_display_path(report_path),
        snapshot_iso=snapshot_dir.name,
        evidence={
            k: report[k]
            for k in (
                "rows_written",
                "rows_by_family",
                "unclassified_count",
                "families_with_zero_coverage",
                "row_count_delta_pct",
            )
            if k in report
        },
    )


def _age_hours_from_receipt(receipt_path: Path) -> float | None:
    if not receipt_path.exists():
        return None
    try:
        body = json.loads(receipt_path.read_text())
        fetched_at_raw = body.get("fetched_at", "")
        if not fetched_at_raw:
            return None
        return snapshot_age_hours(fetched_at_raw)
    except (json.JSONDecodeError, ValueError, KeyError):
        return None


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _derive_status(
    *,
    flags: list[DriftFlag],
    age_hours: float | None,
) -> Literal["ok", "warn", "stale", "broken"]:
    if (
        DriftFlag.PROVIDER_UNAVAILABLE in flags
        or DriftFlag.INDEX_REBUILD_FAILED_FELL_BACK in flags
    ):
        return "broken"
    if age_hours is not None and age_hours > STALENESS_THRESHOLD_HOURS:
        return "stale"
    if flags:
        return "warn"
    return "ok"
