"""Parquet index loading + caching.

Centralizes the "find the latest snapshot per provider, read its parquet,
return a polars DataFrame" plumbing so compare() and lookup() do not each
re-implement it.

Per ADR 0001 the parquet is the query target. Per ADR 0007 a row's hourly_usd
column means different things for `row_kind="instance"` vs `row_kind="rate"`;
callers MUST branch on row_kind before aggregating.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterable

import polars as pl

from ingest._shared import store_root


def latest_snapshot_dir(provider: str) -> Path | None:
    """Return the most recent snapshot directory for a provider that has both
    receipt.json and index.parquet present. Returns None if none qualify."""
    root = store_root(provider)
    if not root.exists():
        return None
    candidates: list[Path] = []
    for receipt in root.glob("*/receipt.json"):
        snapshot_dir = receipt.parent
        if (snapshot_dir / "index.parquet").exists():
            candidates.append(snapshot_dir)
    if not candidates:
        return None
    # Snapshot directory names are ISO-compact timestamps; lexical sort is
    # chronological.
    return sorted(candidates, key=lambda p: p.name)[-1]


@lru_cache(maxsize=16)
def load_index(provider: str, snapshot_iso: str) -> pl.DataFrame:
    """Read a provider's parquet at a specific snapshot. Cached so repeat
    compare/lookup calls do not re-parse."""
    parquet = store_root(provider) / snapshot_iso / "index.parquet"
    return pl.read_parquet(parquet)


def load_latest(provider: str) -> tuple[pl.DataFrame, Path] | None:
    """Read the latest available parquet for one provider. Returns (df, dir) or
    None if no usable snapshot."""
    snapshot_dir = latest_snapshot_dir(provider)
    if snapshot_dir is None:
        return None
    return load_index(provider, snapshot_dir.name), snapshot_dir


def load_union(providers: Iterable[str]) -> tuple[pl.DataFrame, dict[str, Path]]:
    """Read the latest parquets for several providers and return one concat'd
    DataFrame plus a {provider: snapshot_dir} map so callers can attach data
    quality info to each provider's contribution."""
    frames: list[pl.DataFrame] = []
    snapshot_dirs: dict[str, Path] = {}
    for p in providers:
        item = load_latest(p)
        if item is None:
            continue
        df, snapshot_dir = item
        frames.append(df)
        snapshot_dirs[p] = snapshot_dir
    if not frames:
        return pl.DataFrame(), snapshot_dirs
    return pl.concat(frames, how="vertical_relaxed"), snapshot_dirs
