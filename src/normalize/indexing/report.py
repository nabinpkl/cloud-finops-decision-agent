"""Index report construction and hard-gate policy."""

from __future__ import annotations

from typing import Any

from normalize.schema import CitationVerification, DriftFlag, IndexReport, IndexRow
from normalize.taxonomy.loader import UNCLASSIFIED, families_for_provider

# Drift thresholds per ADR 0004.
ROW_DROP_HARD_FAIL_PCT = 50.0
ROW_DROP_WARN_PCT = 30.0
CITATION_FAIL_HARD_FAIL_PCT = 1.0


def build_report(
    *,
    provider: str,
    snapshot_iso: str,
    rows: list[IndexRow],
    prev_rows: int | None,
    citation: CitationVerification,
    fp_change: dict[str, list[list[str]]] | None,
) -> IndexReport:
    rows_by_family: dict[str, int] = {}
    unclassified_samples: list[dict[str, Any]] = []
    for row in rows:
        rows_by_family[row.family] = rows_by_family.get(row.family, 0) + 1
        if row.family == UNCLASSIFIED and len(unclassified_samples) < 20:
            unclassified_samples.append(
                {
                    "provider": row.provider,
                    "instance_type": row.instance_type,
                    "region_native": row.region_native,
                }
            )

    unclassified_count = rows_by_family.get(UNCLASSIFIED, 0)
    declared = families_for_provider(provider)
    families_with_zero_coverage = sorted(
        family
        for family, prefixes in declared.items()
        if prefixes and rows_by_family.get(family, 0) == 0
    )

    delta_pct: float | None = None
    if prev_rows is not None and prev_rows > 0:
        delta_pct = (len(rows) - prev_rows) / prev_rows * 100.0

    flags = _report_flags(
        unclassified_count=unclassified_count,
        families_with_zero_coverage=families_with_zero_coverage,
        delta_pct=delta_pct,
        citation=citation,
        fp_change=fp_change,
    )

    return IndexReport(
        provider=provider,
        snapshot_iso=snapshot_iso,
        rows_written=len(rows),
        rows_by_family=rows_by_family,
        unclassified_count=unclassified_count,
        unclassified_samples=unclassified_samples,
        families_with_zero_coverage=families_with_zero_coverage,
        row_count_delta_pct=delta_pct,
        rows_in_previous_index=prev_rows,
        citation_verification=citation,
        flags=flags,
        human_summary=compose_summary(
            provider=provider,
            rows=len(rows),
            unclassified=unclassified_count,
            families_with_zero_coverage=families_with_zero_coverage,
            delta_pct=delta_pct,
            citation=citation,
            fp_change=fp_change,
        ),
    )


def passes_hard_gates(report: IndexReport, citation: CitationVerification) -> bool:
    if (
        report.row_count_delta_pct is not None
        and report.row_count_delta_pct <= -ROW_DROP_HARD_FAIL_PCT
    ):
        return False
    if citation.sampled > 0:
        fail_pct = citation.failed / citation.sampled * 100.0
        if fail_pct > CITATION_FAIL_HARD_FAIL_PCT:
            return False
    return True


def compose_summary(
    *,
    provider: str,
    rows: int,
    unclassified: int,
    families_with_zero_coverage: list[str],
    delta_pct: float | None,
    citation: CitationVerification,
    fp_change: dict[str, list[list[str]]] | None,
) -> str:
    parts: list[str] = []
    if unclassified > 0:
        parts.append(
            f"{unclassified} {provider} shapes in this snapshot are not yet in our taxonomy; "
            "the comparison excludes them."
        )
    if families_with_zero_coverage:
        parts.append(
            f"Families with zero {provider} coverage this snapshot: "
            f"{', '.join(families_with_zero_coverage)}."
        )
    if delta_pct is not None and delta_pct <= -ROW_DROP_WARN_PCT:
        parts.append(f"Row count dropped {abs(delta_pct):.1f}% vs the previous {provider} snapshot.")
    if citation.failed > 0:
        parts.append(
            f"Citation verification failed on {citation.failed} of {citation.sampled} sampled rows."
        )
    if fp_change and (fp_change["added"] or fp_change["removed"] or fp_change["type_changed"]):
        parts.append("Upstream schema fingerprint shifted; see fingerprint diff in the report.")
    if not parts:
        return f"{provider} index built clean: {rows} rows."
    return " ".join(parts)


def _report_flags(
    *,
    unclassified_count: int,
    families_with_zero_coverage: list[str],
    delta_pct: float | None,
    citation: CitationVerification,
    fp_change: dict[str, list[list[str]]] | None,
) -> list[str]:
    flags: list[str] = []
    if fp_change and (fp_change["added"] or fp_change["removed"] or fp_change["type_changed"]):
        flags.append(DriftFlag.SCHEMA_DRIFT)
    if unclassified_count > 0:
        flags.append(DriftFlag.NEW_UNCLASSIFIED_SHAPES)
    if families_with_zero_coverage:
        flags.append(DriftFlag.FAMILY_COVERAGE_GAP)
    if delta_pct is not None and delta_pct <= -ROW_DROP_WARN_PCT:
        flags.append(DriftFlag.ROW_COUNT_DROP)
    if citation.sampled > 0 and citation.failed > 0:
        flags.append(DriftFlag.CITATION_VERIFICATION_PARTIAL)
    return flags

