"""CLI wrapper for index builds."""

from __future__ import annotations

import argparse
import json
import sys

from project_paths import PROJECT_ROOT
from normalize.indexing import SUPPORTED_PROVIDERS, build_provider


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a parquet index for one provider's latest snapshot.")
    parser.add_argument("provider", choices=SUPPORTED_PROVIDERS, help="Provider to index.")
    parser.add_argument("--force", action="store_true", help="Rebuild even if index.parquet already exists.")
    args = parser.parse_args()

    result = build_provider(args.provider, force=args.force)
    print(
        json.dumps(
            {
                "provider": result.provider,
                "snapshot_iso": result.snapshot_iso,
                "success": result.success,
                "parquet_path": str(result.parquet_path.relative_to(PROJECT_ROOT)),
                "report_path": str(result.report_path.relative_to(PROJECT_ROOT)),
                "rows_written": result.report.rows_written,
                "flags": result.report.flags,
                "human_summary": result.report.human_summary,
                "error": result.error,
            },
            indent=2,
        )
    )
    sys.exit(0 if result.success else 1)
