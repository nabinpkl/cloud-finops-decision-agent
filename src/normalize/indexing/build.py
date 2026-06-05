"""Index build orchestrator.

Per ADR 0002 the build seam lives here, not in gates."""

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
from normalize.indexing.report import build_report, passes_hard_gates
from normalize.schema import IndexReport
from normalize.indexing.verifier import verify

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

    snapshot_dir = latest_snapshot_dir(provider)
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

    builder = load_builder(provider)
    output = builder.build(snapshot_dir)

    write_parquet(output.rows, parquet_path)

    prev_fp = previous_fingerprint(provider, snapshot_dir)
    fp_write(output.fingerprint, fingerprint_path)
    fp_change = fp_diff(prev_fp, output.fingerprint) if prev_fp is not None else None

    prev_rows = previous_row_count(provider, snapshot_dir)
    citation = verify(output.rows)
    report = build_report(
        provider=provider,
        snapshot_iso=snapshot_dir.name,
        rows=output.rows,
        prev_rows=prev_rows,
        citation=citation,
        fp_change=fp_change,
    )
    report_path.write_text(json.dumps(report.to_dict(), indent=2))

    success = passes_hard_gates(report, citation)
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

def main() -> None:
    from normalize.indexing.cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()
