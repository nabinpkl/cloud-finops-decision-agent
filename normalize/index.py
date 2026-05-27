"""Index orchestrator.

Reads a provider's snapshot directory, dispatches to the per-provider builder,
writes index.parquet, schema_fingerprint.json, and index_report.json. Computes
drift flags by diffing against the previous snapshot when available.

Per ADR 0002 the orchestrator lives here, not in gates."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from gates._shared import PROJECT_ROOT, store_root
from normalize.builders import BuilderOutput
from normalize.fingerprint import diff as fp_diff
from normalize.fingerprint import read as fp_read
from normalize.fingerprint import write as fp_write
from normalize.schema import (
    INDEX_SCHEMA,
    CitationVerification,
    DriftFlag,
    IndexReport,
    IndexRow,
)
from normalize.taxonomy_loader import UNCLASSIFIED, families_for_provider
from normalize.verifier import verify

# Drift thresholds per ADR 0004.
ROW_DROP_HARD_FAIL_PCT = 50.0
ROW_DROP_WARN_PCT = 30.0
CITATION_FAIL_HARD_FAIL_PCT = 1.0

SUPPORTED_PROVIDERS = ["linode", "vultr", "azure", "ibm", "aws", "gcp", "oracle"]  # all v0 providers


@dataclass
class BuildResult:
    provider: str
    snapshot_iso: str
    parquet_path: Path
    fingerprint_path: Path
    report_path: Path
    report: IndexReport
    success: bool
    error: str | None = None


def build_provider(provider: str, *, force: bool = False) -> BuildResult:
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"no builder registered for {provider!r} (supported: {SUPPORTED_PROVIDERS})")

    snapshot_dir = _latest_snapshot_dir(provider)
    if snapshot_dir is None:
        raise FileNotFoundError(f"no snapshot found under store/{provider}/")

    parquet_path = snapshot_dir / "index.parquet"
    fingerprint_path = snapshot_dir / "schema_fingerprint.json"
    report_path = snapshot_dir / "index_report.json"

    if parquet_path.exists() and report_path.exists() and not force:
        report = IndexReport(**json.loads(report_path.read_text()))
        return BuildResult(
            provider=provider,
            snapshot_iso=snapshot_dir.name,
            parquet_path=parquet_path,
            fingerprint_path=fingerprint_path,
            report_path=report_path,
            report=report,
            success=True,
        )

    builder = _load_builder(provider)
    output: BuilderOutput = builder.build(snapshot_dir)

    # Write parquet.
    _write_parquet(output.rows, parquet_path)

    # Write fingerprint and compute diff vs previous snapshot.
    prev_fp = _previous_fingerprint(provider, snapshot_dir)
    fp_write(output.fingerprint, fingerprint_path)
    fp_change = fp_diff(prev_fp, output.fingerprint) if prev_fp is not None else None

    # Coverage report.
    prev_rows = _previous_row_count(provider, snapshot_dir)
    citation = verify(output.rows)
    report = _build_report(
        provider=provider,
        snapshot_iso=snapshot_dir.name,
        rows=output.rows,
        prev_rows=prev_rows,
        citation=citation,
        fp_change=fp_change,
    )
    report_path.write_text(json.dumps(report.to_dict(), indent=2))

    success = _passes_hard_gates(report, citation)
    return BuildResult(
        provider=provider,
        snapshot_iso=snapshot_dir.name,
        parquet_path=parquet_path,
        fingerprint_path=fingerprint_path,
        report_path=report_path,
        report=report,
        success=success,
        error=None if success else "hard-fail gate tripped; see flags in report",
    )


def _latest_snapshot_dir(provider: str) -> Path | None:
    root = store_root(provider)
    if not root.exists():
        return None
    snapshots = sorted(p for p in root.glob("*/receipt.json"))
    return snapshots[-1].parent if snapshots else None


def _previous_fingerprint(provider: str, current: Path) -> list[list[str]] | None:
    root = store_root(provider)
    candidates = sorted(p for p in root.glob("*/schema_fingerprint.json") if p.parent != current)
    return fp_read(candidates[-1]) if candidates else None


def _previous_row_count(provider: str, current: Path) -> int | None:
    root = store_root(provider)
    candidates = sorted(p for p in root.glob("*/index_report.json") if p.parent != current)
    if not candidates:
        return None
    body = json.loads(candidates[-1].read_text())
    return body.get("rows_written")


def _load_builder(provider: str):
    return importlib.import_module(f"normalize.builders.{provider}")


def _write_parquet(rows: list[IndexRow], path: Path) -> None:
    records = [r.as_record() for r in rows]
    df = pl.DataFrame(records, schema=INDEX_SCHEMA, orient="row")
    df.write_parquet(path, compression="zstd")


def _build_report(
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
    for r in rows:
        rows_by_family[r.family] = rows_by_family.get(r.family, 0) + 1
        if r.family == UNCLASSIFIED and len(unclassified_samples) < 20:
            unclassified_samples.append(
                {
                    "provider":      r.provider,
                    "instance_type": r.instance_type,
                    "region_native": r.region_native,
                }
            )

    unclassified_count = rows_by_family.get(UNCLASSIFIED, 0)

    # Coverage gap: a family that taxonomy lists for this provider but produced
    # zero rows. Empty prefix lists are deliberate gaps, not coverage gaps;
    # skip those.
    declared = families_for_provider(provider)
    families_with_zero_coverage = sorted(
        family
        for family, prefixes in declared.items()
        if prefixes and rows_by_family.get(family, 0) == 0
    )

    delta_pct: float | None = None
    if prev_rows is not None and prev_rows > 0:
        delta_pct = (len(rows) - prev_rows) / prev_rows * 100.0

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
        human_summary=_compose_summary(
            provider=provider,
            rows=len(rows),
            unclassified=unclassified_count,
            families_with_zero_coverage=families_with_zero_coverage,
            delta_pct=delta_pct,
            citation=citation,
            fp_change=fp_change,
        ),
    )


def _compose_summary(
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
            f"Families with zero {provider} coverage this snapshot: {', '.join(families_with_zero_coverage)}."
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


def _passes_hard_gates(report: IndexReport, citation: CitationVerification) -> bool:
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a parquet index for one provider's latest snapshot.")
    parser.add_argument("provider", choices=SUPPORTED_PROVIDERS, help="Provider to index.")
    parser.add_argument("--force", action="store_true", help="Rebuild even if index.parquet already exists.")
    args = parser.parse_args()

    result = build_provider(args.provider, force=args.force)
    print(json.dumps(
        {
            "provider":         result.provider,
            "snapshot_iso":     result.snapshot_iso,
            "success":          result.success,
            "parquet_path":     str(result.parquet_path.relative_to(PROJECT_ROOT)),
            "report_path":      str(result.report_path.relative_to(PROJECT_ROOT)),
            "rows_written":     result.report.rows_written,
            "flags":            result.report.flags,
            "human_summary":    result.report.human_summary,
            "error":            result.error,
        },
        indent=2,
    ))
    sys.exit(0 if result.success else 1)


if __name__ == "__main__":
    main()
