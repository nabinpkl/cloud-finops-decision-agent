"""Index-build filesystem artifacts."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import polars as pl

from ingest._shared import store_root
from normalize.indexing.fingerprint import read as fp_read
from normalize.schema import INDEX_SCHEMA, IndexRow


def latest_snapshot_dir(provider: str) -> Path | None:
    root = store_root(provider)
    if not root.exists():
        return None
    snapshots = sorted(path for path in root.glob("*/receipt.json"))
    return snapshots[-1].parent if snapshots else None


def previous_fingerprint(provider: str, current: Path) -> list[list[str]] | None:
    previous = _previous_accepted(provider, current)
    return fp_read(previous / "schema_fingerprint.json") if previous is not None else None


def previous_row_count(provider: str, current: Path) -> int | None:
    previous = _previous_accepted(provider, current)
    if previous is None:
        return None
    body = json.loads((previous / "index_report.json").read_text())
    return body.get("rows_written")


def _previous_accepted(provider: str, current: Path) -> Path | None:
    """The newest snapshot older than `current` whose index was accepted and served.
    A rejected build is never a baseline, or one bad snapshot would excuse the next."""
    root = store_root(provider)
    candidates = sorted(
        path.parent
        for path in root.glob("*/index.parquet")
        if path.parent.name < current.name
        and (path.parent / "index_report.json").exists()
        and (path.parent / "schema_fingerprint.json").exists()
    )
    return candidates[-1] if candidates else None


def load_builder(provider: str):
    return importlib.import_module(f"normalize.builders.{provider}")


def write_parquet(rows: list[IndexRow], path: Path) -> None:
    records = [row.as_record() for row in rows]
    df = pl.DataFrame(records, schema=INDEX_SCHEMA, orient="row")
    df.write_parquet(path, compression="zstd")

