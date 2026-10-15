"""The raw files of one snapshot, as its receipt lists them, and their store paths.

Builders read provider responses only through here: the receipt is the list of
what was fetched (name, request URL, size), so a builder never globs a
directory and never mistakes a derived file for a raw one. A `store_path`
(`store/<provider>/<ISO>/<file>`) is the logical address citations carry; it
resolves against the configured store root, wherever that is.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ingest._shared import store_root

RAW_RECEIPT_VERSION = 2


class SnapshotError(Exception):
    """A snapshot directory cannot be read as a raw-first snapshot."""


@dataclass(frozen=True)
class SnapshotFile:
    name: str
    path: Path
    url: str
    store_path: str


def require_raw_receipt(snapshot_dir: Path) -> dict:
    receipt_path = snapshot_dir / "receipt.json"
    if not receipt_path.is_file():
        raise SnapshotError(f"{store_path_for(snapshot_dir, 'receipt.json')}: no receipt; the fetch never finished")
    receipt = json.loads(receipt_path.read_text())
    if receipt.get("receipt_version") != RAW_RECEIPT_VERSION:
        raise SnapshotError(
            f"{store_path_for(snapshot_dir, 'receipt.json')} predates raw-first ingest "
            f"(receipt_version {receipt.get('receipt_version')!r}); refetch this provider"
        )
    return receipt


def snapshot_files(snapshot_dir: Path) -> list[SnapshotFile]:
    """Every raw file the receipt lists, in receipt order, each checked present and whole."""
    receipt = require_raw_receipt(snapshot_dir)
    files: list[SnapshotFile] = []
    for entry in receipt["files"]:
        name = entry["name"]
        path = snapshot_dir / name
        if not path.is_file():
            raise SnapshotError(f"{store_path_for(snapshot_dir, name)}: listed in the receipt but missing")
        if path.stat().st_size != entry["size_bytes"]:
            raise SnapshotError(
                f"{store_path_for(snapshot_dir, name)}: {path.stat().st_size} bytes on disk, "
                f"{entry['size_bytes']} in the receipt"
            )
        files.append(SnapshotFile(name=name, path=path, url=entry["url"], store_path=store_path_for(snapshot_dir, name)))
    return files


def store_path_for(snapshot_dir: Path, name: str) -> str:
    return f"store/{snapshot_dir.parent.name}/{snapshot_dir.name}/{name}"


def resolve_store_path(store_path: str) -> Path:
    parts = store_path.split("/")
    if len(parts) != 4 or parts[0] != "store" or any(p in ("", ".", "..") for p in parts[1:]):
        raise SnapshotError(f"not a store path: {store_path!r}")
    _, provider, snapshot_iso, name = parts
    return store_root(provider) / snapshot_iso / name
