"""Mocked integration tests for normalize.data_quality.

A tiny on-disk store is built under tmp_path and the configured ingest store
root is patched to point at it.
This exercises the real age/status logic, including the 24h staleness boundary
and the UTC-aware parse that AGENTS.md warns about.
"""

from __future__ import annotations

import ingest._shared as shared
import normalize.data_quality as dq
from helpers import iso_hours_ago, make_snapshot

REPORT = {
    "flags": [],
    "human_summary": "built clean",
    "rows_written": 10,
    "rows_by_family": {"general-purpose": 10},
    "unclassified_count": 0,
    "families_with_zero_coverage": [],
    "row_count_delta_pct": None,
}


def _point_at(monkeypatch, tmp_path):
    store = tmp_path / "store"
    monkeypatch.setattr(shared.ingest_settings, "store_root", str(store))
    return store


def test_fresh_clean_is_ok(monkeypatch, tmp_path):
    store = _point_at(monkeypatch, tmp_path)
    make_snapshot(store, "vultr", "2026-05-27T00-00-00Z",
                  fetched_at=iso_hours_ago(1), report=REPORT)

    env = dq.compute_envelope(["vultr"])

    assert env["overall_status"] == "ok"
    assert env["per_provider"]["vultr"]["status"] == "ok"
    assert env["per_provider"]["vultr"]["flags"] == []


def test_stale_past_24h_boundary(monkeypatch, tmp_path):
    store = _point_at(monkeypatch, tmp_path)
    make_snapshot(store, "vultr", "2026-05-27T00-00-00Z",
                  fetched_at=iso_hours_ago(25), report=REPORT)

    env = dq.compute_envelope(["vultr"])
    pp = env["per_provider"]["vultr"]

    assert pp["status"] == "stale"
    assert "snapshot_stale" in pp["flags"]
    assert "24h freshness threshold" in pp["human_summary"]
    assert pp["snapshot_age_hours"] > 24.0


def test_flag_without_staleness_is_warn(monkeypatch, tmp_path):
    store = _point_at(monkeypatch, tmp_path)
    report = {**REPORT, "flags": ["family_coverage_gap"]}
    make_snapshot(store, "gcp", "2026-05-27T00-00-00Z",
                  fetched_at=iso_hours_ago(2), report=report)

    pp = dq.compute_envelope(["gcp"])["per_provider"]["gcp"]

    assert pp["status"] == "warn"
    assert pp["flags"] == ["family_coverage_gap"]


def test_missing_provider_is_broken(monkeypatch, tmp_path):
    _point_at(monkeypatch, tmp_path)  # empty store, no snapshot written

    pp = dq.compute_envelope(["aws"])["per_provider"]["aws"]

    assert pp["status"] == "broken"
    assert "provider_unavailable" in pp["flags"]
    assert pp["snapshot_age_hours"] is None


def test_rollup_takes_worst_status(monkeypatch, tmp_path):
    store = _point_at(monkeypatch, tmp_path)
    make_snapshot(store, "vultr", "2026-05-27T00-00-00Z",
                  fetched_at=iso_hours_ago(1), report=REPORT)            # ok
    make_snapshot(store, "gcp", "2026-05-27T00-00-00Z",
                  fetched_at=iso_hours_ago(30), report=REPORT)           # stale

    env = dq.compute_envelope(["vultr", "gcp"])

    assert env["per_provider"]["vultr"]["status"] == "ok"
    assert env["per_provider"]["gcp"]["status"] == "stale"
    assert env["overall_status"] == "stale"  # worst of the two
