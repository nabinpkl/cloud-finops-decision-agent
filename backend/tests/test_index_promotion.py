"""An index is served only when its gates accept it; a rejected build leaves the
previous accepted snapshot serving and says so in the data-quality envelope."""

from __future__ import annotations

import copy
from typing import Any

import pytest

import ingest._shared as shared
import normalize.data_quality as dq
from helpers import write_raw_snapshot
from normalize.indexing import build_provider
from normalize.query.loader import latest_snapshot_dir
from test_builders_raw import SNAPSHOTS, vultr_plan

OLD = "2026-10-14T08-00-00Z"
NEW = "2026-10-15T08-00-00Z"
NEWER = "2026-10-16T08-00-00Z"


@pytest.fixture
def store(monkeypatch, tmp_path):
    root = tmp_path / "store"
    monkeypatch.setattr(shared.ingest_settings, "store_root", str(root))
    return root


def azure_with(mutate) -> dict[str, Any]:
    files: dict[str, Any] = copy.deepcopy(SNAPSHOTS["azure"])
    for page in files.values():
        for item in page["Items"]:
            mutate(item)
    return files


def vultr_with(count: int) -> dict:
    plans = [vultr_plan(f"vc2-{n}c-{n * 2}gb", n, n * 2048, 10.0 * n) for n in range(1, count + 1)]
    return {"plans-0001.json": {"plans": plans, "meta": {"links": {"next": "", "prev": ""}}}}


def test_renamed_price_field_is_rejected_and_the_old_index_keeps_serving(store):
    write_raw_snapshot(store, "azure", OLD, SNAPSHOTS["azure"])
    assert build_provider("azure").success
    write_raw_snapshot(store, "azure", NEW, azure_with(lambda item: item.update(retailPriceUSD=item.pop("retailPrice"))))

    result = build_provider("azure")

    assert not result.success
    assert result.report.missing_fields == {"retailPrice": 4}
    assert "fields_missing" in result.report.flags
    assert not (store / "azure" / NEW / "index.parquet").exists()
    assert (store / "azure" / NEW / "index.rejected.parquet").exists()
    assert latest_snapshot_dir("azure") == store / "azure" / OLD

    quality = dq.compute_envelope(["azure"])["per_provider"]["azure"]
    assert quality["status"] == "broken"
    assert "index_rebuild_failed_fell_back" in quality["flags"]
    assert quality["snapshot_iso"] == OLD
    assert NEW in quality["human_summary"] and "rejected" in quality["human_summary"]


def test_renamed_filter_field_means_no_candidates_and_is_rejected(store):
    write_raw_snapshot(store, "azure", NEW, azure_with(lambda item: item.update(service=item.pop("serviceName"))))

    result = build_provider("azure")

    assert not result.success
    assert result.report.candidates == 0
    assert "no_candidates" in result.report.flags
    assert latest_snapshot_dir("azure") is None
    quality = dq.compute_envelope(["azure"])["per_provider"]["azure"]
    assert quality["flags"] == ["provider_unavailable"]
    assert "rejected" in quality["human_summary"]


def test_a_rejected_build_is_never_the_baseline_for_the_next(store):
    write_raw_snapshot(store, "vultr", OLD, vultr_with(4))
    assert build_provider("vultr").report.rows_written == 12

    write_raw_snapshot(store, "vultr", NEW, vultr_with(1))
    dropped = build_provider("vultr")
    assert not dropped.success
    assert "row_count_drop" in dropped.report.flags

    write_raw_snapshot(store, "vultr", NEWER, vultr_with(4))
    recovered = build_provider("vultr")
    assert recovered.success
    assert recovered.report.rows_in_previous_index == 12
    assert latest_snapshot_dir("vultr") == store / "vultr" / NEWER


def test_forced_rebuild_that_now_fails_stops_serving_that_snapshot(store):
    snapshot_dir = write_raw_snapshot(store, "azure", NEW, SNAPSHOTS["azure"])
    assert build_provider("azure").success
    write_raw_snapshot(store, "azure", NEW, azure_with(lambda item: item.update(retailPriceUSD=item.pop("retailPrice"))))

    assert not build_provider("azure", force=True).success
    assert not (snapshot_dir / "index.parquet").exists()


def test_cached_report_is_returned_without_rebuilding(store):
    write_raw_snapshot(store, "oracle", NEW, SNAPSHOTS["oracle"])
    first = build_provider("oracle")
    (store / "oracle" / NEW / "products.json").write_text("not json anymore")

    again = build_provider("oracle")

    assert again.success and again.report == first.report
