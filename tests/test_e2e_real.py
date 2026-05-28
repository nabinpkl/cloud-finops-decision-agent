"""End-to-end tests against the real snapshots in store/.

Marked `e2e` and skipped entirely when nothing is indexed (e.g. a fresh clone
or CI without a populated store). These run the full stack unmocked: real
parquet -> query -> FastAPI wire layer -> citation excerpt against the real
upstream JSON files (including the ~200 MB AWS region file).

Prices change between fetches and snapshots go stale, so the assertions are
contract invariants, not hardcoded dollar values. The headline test closes the
citation loop: the excerpt of a returned citation must resolve to the very
number the API quoted. That is "verify by clicking through" enforced.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import api.main as apimain
from normalize.index import SUPPORTED_PROVIDERS
from normalize.loader import latest_snapshot_dir
from normalize.verifier import _extract_price

INDEXED = [p for p in SUPPORTED_PROVIDERS if latest_snapshot_dir(p) is not None]

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not INDEXED, reason="no indexed snapshots in store/"),
]


@pytest.fixture(scope="module")
def client():
    return TestClient(apimain.app)


def _excerpt(client, ref, json_path):
    r = client.get("/citation/excerpt", params={**ref, "path": json_path})
    assert r.status_code == 200, r.text
    return r.json()


def _matched_price(matched_value: str) -> float:
    """The cited leaf is sometimes a plain number ("40", "0.1164") and sometimes
    the upstream price object (GCP's {units, nanos}). Interpret it the same way
    the citation verifier does so the excerpt round-trips against the quote."""
    try:
        parsed = json.loads(matched_value)
    except (json.JSONDecodeError, TypeError):
        parsed = matched_value
    price = _extract_price(parsed)
    assert price is not None, f"could not extract a price from excerpt value {matched_value!r}"
    return price


def _compare(client):
    resp = client.post("/compare", json={
        "vcpu": 4, "ram_gb": 8, "region": "eu-central", "family": "general-purpose",
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_compare_returns_ranked_results_with_refs(client):
    data = _compare(client)
    if not data["results"]:
        pytest.skip("no EU general-purpose 4x8 candidates in the current store")

    monthlies = [r["monthly_usd"] for r in data["results"] if r["monthly_usd"] is not None]
    assert monthlies == sorted(monthlies), "results must be ranked cheapest-first"

    for r in data["results"]:
        c = r["citation"]
        constituents = c["composite"] if "composite" in c else [c]
        for entry in constituents:
            assert "store_path" not in entry, "internal store_path leaked to the wire"
            ref = entry["snapshot"]
            assert set(ref) == {"provider", "snapshot_iso", "filename"}
            assert entry["source_url"] and entry["json_path"]
            assert entry["age_hours"] is not None


def test_citation_excerpt_resolves_to_quoted_price(client):
    data = _compare(client)
    if not data["results"]:
        pytest.skip("no EU general-purpose 4x8 candidates in the current store")

    for r in data["results"]:
        c = r["citation"]
        if "composite" in c:
            total = 0.0
            for entry in c["composite"]:
                ex = _excerpt(client, entry["snapshot"], entry["json_path"])
                assert _matched_price(ex["matched_value"]) == pytest.approx(entry["rate"], rel=1e-6), (
                    f"{r['provider']} constituent excerpt {ex['matched_value']} != rate {entry['rate']}"
                )
                total += entry["contribution_usd"]
            assert total == pytest.approx(r["hourly_usd"], rel=1e-6)
        else:
            ex = _excerpt(client, c["snapshot"], c["json_path"])
            val = _matched_price(ex["matched_value"])
            # the cited leaf is either the hourly or the monthly price
            assert (
                val == pytest.approx(r["hourly_usd"], rel=1e-3)
                or val == pytest.approx(r["monthly_usd"], rel=1e-3)
            ), f"{r['provider']} excerpt {val} matches neither hourly nor monthly"


def test_data_quality_envelope_present(client):
    data = _compare(client)
    dq = data["data_quality"]
    assert dq["overall_status"] in {"ok", "warn", "stale", "broken"}
    for p, pp in dq["per_provider"].items():
        assert pp["status"] in {"ok", "warn", "stale", "broken"}
