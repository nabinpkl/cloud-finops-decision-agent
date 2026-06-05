"""Schema fingerprint walker per ADR 0004 layer 1.

A fingerprint is a sorted list of (path_prefix, leaf_type) tuples sampled across
a JSON document up to a bounded depth. Diffing two fingerprints surfaces field
additions, removals, and type changes between consecutive snapshots cheaply.

For nested providers (IBM especially) the caller fingerprints multiple sub-trees
separately and merges them under named keys."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

# Bounded walk: how many levels of nesting to descend, and how many array
# elements to sample at each list level. Together these keep the fingerprint
# small (a few hundred tuples) even on AWS-sized files.
MAX_DEPTH = 5
ARRAY_SAMPLE = 8


def fingerprint(doc: Any, *, max_depth: int = MAX_DEPTH, array_sample: int = ARRAY_SAMPLE) -> list[list[str]]:
    """Walk doc to a bounded depth, return a sorted list of [path, type] pairs.
    Returned as a list-of-pairs (not dict) so JSON output stays stable and diffs cleanly."""
    seen: set[tuple[str, str]] = set()
    rng = random.Random(0)
    _walk(doc, path="$", depth=0, max_depth=max_depth, array_sample=array_sample, rng=rng, seen=seen)
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
    path.write_text(json.dumps({"version": 1, "entries": fp}, indent=2))


def read(path: Path) -> list[list[str]] | None:
    if not path.exists():
        return None
    body = json.loads(path.read_text())
    return body.get("entries", [])


def _walk(
    node: Any,
    *,
    path: str,
    depth: int,
    max_depth: int,
    array_sample: int,
    rng: random.Random,
    seen: set[tuple[str, str]],
) -> None:
    if depth >= max_depth:
        return
    if isinstance(node, dict):
        seen.add((path, "object"))
        for key, value in node.items():
            child_path = f"{path}.{key}"
            seen.add((child_path, _typename(value)))
            _walk(value, path=child_path, depth=depth + 1, max_depth=max_depth, array_sample=array_sample, rng=rng, seen=seen)
    elif isinstance(node, list):
        seen.add((path, "array"))
        if not node:
            return
        sample = node if len(node) <= array_sample else rng.sample(node, k=array_sample)
        for item in sample:
            child_path = f"{path}[*]"
            seen.add((child_path, _typename(item)))
            _walk(item, path=child_path, depth=depth + 1, max_depth=max_depth, array_sample=array_sample, rng=rng, seen=seen)


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
        return "object"
    return type(value).__name__
