"""Index build orchestrator.

Per ADR 0002 the build seam lives here, not in ingest."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from normalize.indexing.fingerprint import diff as fp_diff
from normalize.indexing.fingerprint import write as fp_write
from normalize.indexing.artifacts import (
    latest_snapshot_dir,
    load_builder,
    previous_fingerprint,
    previous_row_count,
    write_parquet,
)
from normalize.indexing.report import build_report
from normalize.schema import IndexReport
from normalize.indexing.verifier import verify
from normalize.snapshot_files import require_raw_receipt

SUPPORTED_PROVIDERS = [
    "linode", "vultr", "azure", "ibm", "aws", "gcp", "oracle",
    "scaleway", "ovh", "exoscale", "cherryservers", "runpod",
]


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
    """Index the provider's newest complete snapshot. The parquet is written beside
    the snapshot as `index.parquet` only when the hard gates accept it; a rejected
    build lands as `index.rejected.parquet` and the previous accepted snapshot
    keeps serving (ADR 0004, ADR 0018)."""
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"no builder registered for {provider!r} (supported: {SUPPORTED_PROVIDERS})")

    snapshot_dir = latest_snapshot_dir(provider)
    if snapshot_dir is None:
        raise FileNotFoundError(f"no snapshot found under store/{provider}/")
    require_raw_receipt(snapshot_dir)

    parquet_path = snapshot_dir / "index.parquet"
    rejected_path = snapshot_dir / "index.rejected.parquet"
    fingerprint_path = snapshot_dir / "schema_fingerprint.json"
    report_path = snapshot_dir / "index_report.json"

    if report_path.exists() and not force:
        report = IndexReport(**json.loads(report_path.read_text()))
        if report.accepted == parquet_path.exists():
            return _result(provider, snapshot_dir, report)

    builder = load_builder(provider)
    output = builder.build(snapshot_dir)

    pending_path = snapshot_dir / "index.parquet.part"
    write_parquet(output.rows, pending_path)

    prev_fp = previous_fingerprint(provider, snapshot_dir)
    fp_write(output.fingerprint, fingerprint_path)
    fp_change = fp_diff(prev_fp, output.fingerprint) if prev_fp is not None else None

    prev_rows = previous_row_count(provider, snapshot_dir)
    citation = verify(output.rows)
    report = build_report(
        provider=provider,
        snapshot_iso=snapshot_dir.name,
        rows=output.rows,
        candidates=output.candidates,
        missing_fields=output.missing_fields,
        prev_rows=prev_rows,
        citation=citation,
        fp_change=fp_change,
    )
    report_path.write_text(json.dumps(report.to_dict(), indent=2))

    if report.accepted:
        rejected_path.unlink(missing_ok=True)
        pending_path.replace(parquet_path)
    else:
        parquet_path.unlink(missing_ok=True)
        pending_path.replace(rejected_path)
    return _result(provider, snapshot_dir, report)


def _result(provider: str, snapshot_dir: Path, report: IndexReport) -> BuildResult:
    return BuildResult(
        provider=provider,
        snapshot_iso=snapshot_dir.name,
        parquet_path=snapshot_dir / ("index.parquet" if report.accepted else "index.rejected.parquet"),
        fingerprint_path=snapshot_dir / "schema_fingerprint.json",
        report_path=snapshot_dir / "index_report.json",
        report=report,
        success=report.accepted,
        error=None if report.accepted else "hard gate rejected the index; the previous accepted snapshot keeps serving",
    )
