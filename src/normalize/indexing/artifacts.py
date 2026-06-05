"""Index-build filesystem artifacts."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import polars as pl

from gates._shared import store_root
from normalize.indexing.fingerprint import read as fp_read
from normalize.schema import INDEX_SCHEMA, IndexRow


def latest_snapshot_dir(provider: str) -> Path | None:
    root = store_root(provider)
    if not root.exists():
        return None
    snapshots = sorted(path for path in root.glob("*/receipt.json"))
    return snapshots[-1].parent if snapshots else None


def previous_fingerprint(provider: str, current: Path) -> list[list[str]] | None:
    root = store_root(provider)
    candidates = sorted(
        path for path in root.glob("*/schema_fingerprint.json") if path.parent != current
    )
    return fp_read(candidates[-1]) if candidates else None


def previous_row_count(provider: str, current: Path) -> int | None:
    root = store_root(provider)
    candidates = sorted(
        path for path in root.glob("*/index_report.json") if path.parent != current
    )
    if not candidates:
        return None
    body = json.loads(candidates[-1].read_text())
    return body.get("rows_written")


def load_builder(provider: str):
    return importlib.import_module(f"normalize.builders.{provider}")


def write_parquet(rows: list[IndexRow], path: Path) -> None:
    records = [row.as_record() for row in rows]
    df = pl.DataFrame(records, schema=INDEX_SCHEMA, orient="row")
    df.write_parquet(path, compression="zstd")

