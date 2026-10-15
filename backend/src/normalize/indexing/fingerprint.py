"""Schema fingerprint walker per ADR 0004 layer 1.

A fingerprint is a sorted list of (path_prefix, leaf_type) tuples sampled across
a JSON document up to a bounded depth. Diffing two fingerprints surfaces field
additions, removals, and type changes between consecutive snapshots cheaply.

Objects come in two kinds. A *record* has field names as keys (`attributes`,
`pricePerUnit`); each key is part of the schema and gets its own path. A *map*
is keyed by data (AWS SKUs, offer and rate codes, IBM plan UUIDs, region codes);
its keys say nothing about the schema, so the map collapses to one `{*}` path
and only a few of its values are walked, like an array. Without that, AWS's
per-SKU keys made a fingerprint of ~1.5 GB that changed with every new SKU.

Sampling is deterministic and stable across snapshots: arrays and maps take
their first ARRAY_SAMPLE elements (maps in sorted key order), so two snapshots of
an unchanged schema yield the same fingerprint."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# Bounded walk: how many levels of nesting to descend, and how many array or map
# elements to sample at each level. Together these keep the fingerprint to a few
# hundred tuples even on AWS-sized files.
MAX_DEPTH = 8
ARRAY_SAMPLE = 8
VERSION = 2

# An object with more keys than this is a map whatever its keys look like.
MAP_KEY_COUNT = 64
# A key that carries a digit and is at least 8 characters of identifier
# characters is data, not a field name: SKUs (4AQFBS47E7Y26RTA), offer codes
# (4AQFBS47E7Y26RTA.JRTCKXETXF), UUIDs, region codes (ap-southeast-1).
ID_KEY_RE = re.compile(r"^(?=.*\d)[A-Za-z0-9._:-]{8,}$")


def fingerprint(doc: Any, *, max_depth: int = MAX_DEPTH, array_sample: int = ARRAY_SAMPLE) -> list[list[str]]:
    """Walk doc to a bounded depth, return a sorted list of [path, type] pairs.
    Returned as a list-of-pairs (not dict) so JSON output stays stable and diffs cleanly."""
    seen: set[tuple[str, str]] = set()
    _walk(doc, path="$", depth=0, max_depth=max_depth, array_sample=array_sample, seen=seen)
    return sorted([[p, t] for (p, t) in seen])


def diff(prev: list[list[str]], curr: list[list[str]]) -> dict[str, list[list[str]]]:
    """Return {added, removed, type_changed} between two fingerprints."""
    prev_paths = {p: t for p, t in prev}
    curr_paths = {p: t for p, t in curr}

    added: list[list[str]] = []
    removed: list[list[str]] = []
    type_changed: list[list[str]] = []

    for p, t in curr_paths.items():
        if p not in prev_paths:
            added.append([p, t])
        elif prev_paths[p] != t:
            type_changed.append([p, f"{prev_paths[p]} -> {t}"])
    for p, t in prev_paths.items():
        if p not in curr_paths:
            removed.append([p, t])

    return {
        "added":        sorted(added),
        "removed":      sorted(removed),
        "type_changed": sorted(type_changed),
    }


def write(fp: list[list[str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": VERSION, "entries": fp}, indent=2))


def read(path: Path) -> list[list[str]] | None:
    """The fingerprint at path, or None when absent or written by another version of
    the walker: fingerprints from different walkers are not comparable. The
    version is read from the file's first bytes, so an old multi-gigabyte
    fingerprint is never loaded just to be ignored."""
    if not path.exists():
        return None
    with path.open("rb") as fh:
        head = fh.read(64).decode("utf-8", errors="replace")
    match = re.match(r'\s*\{\s*"version"\s*:\s*(\d+)', head)
    if match is None or int(match.group(1)) != VERSION:
        return None
    body = json.loads(path.read_text())
    return body.get("entries", [])


def is_map(node: dict[str, Any]) -> bool:
    return len(node) > MAP_KEY_COUNT or (bool(node) and all(ID_KEY_RE.match(key) for key in node))


def _walk(
    node: Any,
    *,
    path: str,
    depth: int,
    max_depth: int,
    array_sample: int,
    seen: set[tuple[str, str]],
) -> None:
    if depth >= max_depth:
        return
    if isinstance(node, dict):
        if is_map(node):
            seen.add((path, "map"))
            child_path = f"{path}.{{*}}"
            for key in sorted(node)[:array_sample]:
                value = node[key]
                seen.add((child_path, _typename(value)))
                _walk(value, path=child_path, depth=depth + 1, max_depth=max_depth, array_sample=array_sample, seen=seen)
            return
        seen.add((path, "object"))
        for key, value in node.items():
            child_path = f"{path}.{key}"
            seen.add((child_path, _typename(value)))
            _walk(value, path=child_path, depth=depth + 1, max_depth=max_depth, array_sample=array_sample, seen=seen)
    elif isinstance(node, list):
        seen.add((path, "array"))
        child_path = f"{path}[*]"
        for item in node[:array_sample]:
            seen.add((child_path, _typename(item)))
            _walk(item, path=child_path, depth=depth + 1, max_depth=max_depth, array_sample=array_sample, seen=seen)


def _typename(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "map" if is_map(value) else "object"
    return type(value).__name__
