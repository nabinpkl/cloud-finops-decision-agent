"""Mocked integration tests for normalize.citation_excerpt.

Tiny JSON files written to tmp_path drive each branch: a normal match inside a
small parent, the minimal fallback for an oversized parent, an object-valued
leaf, and the resolve-to-nothing error path.
"""

from __future__ import annotations

import json
from pathlib import Path

from normalize.citation_excerpt import PARENT_ENTRY_CAP, build_excerpt


def _write(tmp_path: Path, name: str, obj) -> Path:
    p = tmp_path / name
    p.write_bytes(json.dumps(obj).encode())
    return p


def test_match_shows_parent_context(tmp_path):
    obj = {"plans": [{"id": "vc2-4c-8gb", "bandwidth": 4096, "monthly_cost": 40, "hourly_cost": 0.055}]}
    p = _write(tmp_path, "plans.json", obj)

    out = build_excerpt(abs_path=p, json_path="$.plans[?(@.id=='vc2-4c-8gb')].monthly_cost", context=2)

    assert out["matched_value"] == "40"
    matched = [ln for ln in out["lines"] if ln.get("match")]
    assert len(matched) == 1
    assert '"monthly_cost": 40' in matched[0]["text"]
    # neighbouring keys are visible in the window
    texts = " ".join(ln["text"] for ln in out["lines"])
    assert "bandwidth" in texts and "hourly_cost" in texts
    # line numbers are 1-based and contiguous
    ns = [ln["n"] for ln in out["lines"]]
    assert ns == list(range(ns[0], ns[0] + len(ns)))


def test_object_valued_leaf_stringifies(tmp_path):
    obj = {"rate": {"units": "0", "nanos": 31611000}}
    p = _write(tmp_path, "gcp.json", obj)

    out = build_excerpt(abs_path=p, json_path="$.rate", context=1)

    # the leaf is the {units, nanos} object; matched_value is its JSON form
    assert "units" in out["matched_value"] and "nanos" in out["matched_value"]
    assert any(ln.get("match") for ln in out["lines"])


def test_oversized_parent_falls_back_to_minimal(tmp_path):
    big = {f"k{i}": i for i in range(PARENT_ENTRY_CAP + 5)}
    obj = {"bucket": big}
    p = _write(tmp_path, "big.json", obj)

    out = build_excerpt(abs_path=p, json_path="$.bucket.k7", context=3)

    assert out["matched_value"] == "7"
    assert len(out["lines"]) == 1  # minimal rendering: just the matched key/value
    assert out["lines"][0]["match"] is True


def test_resolve_to_nothing_is_error(tmp_path):
    p = _write(tmp_path, "x.json", {"a": 1})

    out = build_excerpt(abs_path=p, json_path="$.does.not.exist", context=2)

    assert out["lines"] == []
    assert "error" in out
    assert out["matched_value"] is None


def test_missing_file_is_error(tmp_path):
    out = build_excerpt(abs_path=tmp_path / "nope.json", json_path="$.a", context=2)

    assert "error" in out
    assert out["lines"] == []
