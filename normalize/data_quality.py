"""Builds the data_quality envelope per ADR 0005.

Reads each provider's index_report.json and receipt.json, computes
per-provider status (ok | warn | stale | broken), surfaces flags and
human_summary, and rolls up to overall_status.

`age_hours` is computed in timezone-aware UTC per AGENTS.md's UTC-parsing
pitfall."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gates._shared import PROJECT_ROOT
from normalize.loader import latest_snapshot_dir

STALENESS_THRESHOLD_HOURS = 24.0

# Status ordering for the rollup: rightmost wins when comparing.
_STATUS_ORDER = ["ok", "warn", "stale", "broken"]


@dataclass
class ProviderQuality:
    status: str
    snapshot_age_hours: float | None
    flags: list[str]
    human_summary: str
    report_path: str | None = None
    snapshot_iso: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "status":             self.status,
            "snapshot_age_hours": self.snapshot_age_hours,
            "flags":              self.flags,
            "human_summary":      self.human_summary,
        }
        if self.report_path:
            out["report_path"] = self.report_path
        if self.snapshot_iso:
            out["snapshot_iso"] = self.snapshot_iso
        if self.evidence:
            out["evidence"] = self.evidence
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
            flags=["provider_unavailable"],
            human_summary=f"No usable {provider} snapshot found; the provider was excluded from this response.",
        )

    receipt_path = snapshot_dir / "receipt.json"
    report_path = snapshot_dir / "index_report.json"

    age_hours = _age_hours_from_receipt(receipt_path)
    report = json.loads(report_path.read_text()) if report_path.exists() else {}
    flags = list(report.get("flags", []))
    human_summary = report.get("human_summary", f"{provider} index ready.")

    if age_hours is not None and age_hours > STALENESS_THRESHOLD_HOURS:
        if "snapshot_stale" not in flags:
            flags.append("snapshot_stale")

    status = _derive_status(flags=flags, age_hours=age_hours)
    if status == "stale":
        human_summary = (
            f"{provider} snapshot is {age_hours:.1f}h old, past the 24h freshness threshold. "
            "Re-fetch with `just fetch-force {provider}` for live prices."
        )

    return ProviderQuality(
        status=status,
        snapshot_age_hours=age_hours,
        flags=flags,
        human_summary=human_summary,
        report_path=str(report_path.relative_to(PROJECT_ROOT)),
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
        # Per AGENTS.md: parse as timezone-aware UTC. Trailing Z is the timezone
        # marker; convert to +00:00 for fromisoformat.
        parsed = datetime.fromisoformat(fetched_at_raw.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - parsed).total_seconds() / 3600
    except (json.JSONDecodeError, ValueError, KeyError):
        return None


def _derive_status(*, flags: list[str], age_hours: float | None) -> str:
    if "provider_unavailable" in flags or "index_rebuild_failed_fell_back" in flags:
        return "broken"
    if age_hours is not None and age_hours > STALENESS_THRESHOLD_HOURS:
        return "stale"
    if flags:
        return "warn"
    return "ok"
