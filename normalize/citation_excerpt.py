"""Serve-time citation excerpt builder per ADR 0008.

Given a snapshot file and a json_path, render the cited value in context as a
code hunk: the matched value's immediate parent container, pretty-printed with
line numbers, the matched line flagged. This is the "verify by clicking through"
surface. It is computed lazily, only for citations a user actually opens, never
precomputed into the parquet.

Line numbers are local to the cited container and against our canonical
indent=2 rendering, not the raw upstream file (AWS/GCP snapshots arrive
minified, so there is no upstream line numbering to honor). The caller surfaces
this via the `rendering` field.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import orjson
from jsonpath_ng import Fields, Index
from jsonpath_ng.ext import parse as jsonpath_parse

# A parent container larger than this many direct entries is not pretty-printed
# whole; we fall back to a minimal rendering of just the matched key/value. In
# practice every v0 leaf's immediate parent is a small price/plan object, so the
# guard only fires on unexpected schema shapes.
PARENT_ENTRY_CAP = 200

RENDERING_NOTE = (
    "canonical (indent=2); line numbers are within the cited container, "
    "not the raw upstream file"
)


def build_excerpt(*, abs_path: Path, json_path: str, context: int = 4) -> dict[str, Any]:
    """Return a hunk dict for the value at `json_path` inside `abs_path`.

    Shape:
        {
          "json_path": str,
          "matched_value": str,
          "match_line": int,            # 1-based, within `lines`
          "rendering": str,
          "lines": [{"n": int, "text": str, "match"?: True}, ...],
        }
    On failure returns the same shape with an "error" key and empty "lines".
    """
    try:
        doc = _load_doc(str(abs_path), abs_path.stat().st_mtime_ns)
    except FileNotFoundError:
        return _error(json_path, "snapshot file not found")
    except Exception as exc:  # malformed JSON, permissions, etc.
        return _error(json_path, f"could not read snapshot: {type(exc).__name__}: {exc}")

    try:
        expr = jsonpath_parse(json_path)
    except Exception as exc:
        return _error(json_path, f"invalid json_path: {type(exc).__name__}: {exc}")

    matches = expr.find(doc)
    if not matches:
        return _error(json_path, "json_path resolved to nothing")

    m = matches[0]
    parent = m.context.value if m.context is not None else None
    key = _leaf_key(m)

    if parent is None or _too_large(parent):
        return _minimal(json_path, key, m.value)

    pretty = json.dumps(parent, indent=2, ensure_ascii=False)
    lines = pretty.splitlines()
    match_idx = _find_match_line(lines, key)

    start = max(0, match_idx - context)
    end = min(len(lines), match_idx + context + 1)
    window: list[dict[str, Any]] = []
    for i in range(start, end):
        entry: dict[str, Any] = {"n": i + 1, "text": lines[i]}
        if i == match_idx:
            entry["match"] = True
        window.append(entry)

    return {
        "json_path":     json_path,
        "matched_value": _stringify(m.value),
        "match_line":    match_idx + 1,
        "rendering":     RENDERING_NOTE,
        "lines":         window,
    }


@lru_cache(maxsize=8)
def _load_doc(path: str, _mtime_ns: int) -> Any:
    """Parse a snapshot file. Cached by (path, mtime) so a rebuilt snapshot
    invalidates the entry. mtime is part of the key, not used in the body."""
    return orjson.loads(Path(path).read_bytes())


def _leaf_key(m: Any) -> str | int | None:
    """The dict key or list index of the matched node within its parent."""
    path = m.path
    if isinstance(path, Fields) and path.fields:
        return path.fields[0]
    if isinstance(path, Index):
        return path.index
    return None


def _too_large(parent: Any) -> bool:
    return isinstance(parent, (dict, list)) and len(parent) > PARENT_ENTRY_CAP


def _find_match_line(lines: list[str], key: str | int | None) -> int:
    """Index of the line holding the matched node within the pretty parent.

    For a dict key the matched line is `  "<key>": ...`. For a list index we
    cannot label by key, so fall back to the first line. For a None key (root)
    fall back to the first line."""
    if isinstance(key, str):
        needle = f'"{key}":'
        for i, line in enumerate(lines):
            if line.lstrip().startswith(needle):
                return i
    return 0


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return json.dumps(value)
    return json.dumps(value, ensure_ascii=False)


def _minimal(json_path: str, key: str | int | None, value: Any) -> dict[str, Any]:
    """Fallback rendering when there is no usable parent to show in context."""
    label = json.dumps(key) if isinstance(key, str) else str(key)
    text = f"{label}: {_stringify(value)}" if key is not None else _stringify(value)
    return {
        "json_path":     json_path,
        "matched_value": _stringify(value),
        "match_line":    1,
        "rendering":     RENDERING_NOTE,
        "lines":         [{"n": 1, "text": text, "match": True}],
    }


def _error(json_path: str, reason: str) -> dict[str, Any]:
    return {
        "json_path":     json_path,
        "matched_value": None,
        "match_line":    None,
        "rendering":     RENDERING_NOTE,
        "error":         reason,
        "lines":         [],
    }
