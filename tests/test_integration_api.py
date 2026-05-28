"""Mocked integration tests for the FastAPI wire layer.

compare()/lookup() are stubbed with canned query-layer dicts (store_path
present, as the real layer emits). The assertions pin the API contract: the
internal store_path never reaches the wire, and a logical snapshot ref takes
its place on every citation including composite constituents.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import api.main as apimain

ATOMIC_RESULT = {
    "provider": "aws",
    "instance_type": "a1.xlarge",
    "region_native": "eu-central-1",
    "vcpu_actual": 4,
    "ram_gb_actual": 8.0,
    "hourly_usd": 0.115,
    "monthly_usd": 84.0,
    "considered_count": 3,
    "citation": {
        "source_url": "https://pricing.example/eu-central-1/index.json",
        "store_path": "store/aws/2026-05-27T04-23-36Z/eu-central-1.json",
        "json_path": "$.terms.OnDemand['SKU'].x.USD",
        "fetched_at": "2026-05-27T04:23:36Z",
        "age_hours": 6.2,
    },
}

COMPOSITE_RESULT = {
    "provider": "gcp",
    "instance_type": "n2-custom-4-16384",
    "region_native": "europe-west3",
    "vcpu_actual": 4,
    "ram_gb_actual": 16.0,
    "hourly_usd": 0.16,
    "monthly_usd": 116.8,
    "considered_count": 1,
    "synthesized": True,
    "citation": {
        "synthesis": {"rule": "flex_rules.gcp.n2", "formula": "..."},
        "composite": [
            {
                "kind": "rate", "rate_unit": "per_vcpu_hour", "rate": 0.02, "quantity": 4.0,
                "contribution_usd": 0.08,
                "source_url": "https://billing.example/skus",
                "store_path": "store/gcp/2026-05-27T06-32-56Z/skus.json",
                "json_path": "$.skus[?(@.skuId=='A')].rate",
                "fetched_at": "2026-05-27T06:32:56Z", "age_hours": 4.0,
            },
            {
                "kind": "rate", "rate_unit": "per_gb_ram_hour", "rate": 0.005, "quantity": 16.0,
                "contribution_usd": 0.08,
                "source_url": "https://billing.example/skus",
                "store_path": "store/gcp/2026-05-27T06-32-56Z/skus.json",
                "json_path": "$.skus[?(@.skuId=='B')].rate",
                "fetched_at": "2026-05-27T06:32:56Z", "age_hours": 4.0,
            },
        ],
    },
}

CANNED_COMPARE = {
    "request": {"vcpu": 4, "ram_gb": 16, "region": "eu-central", "family": "general-purpose",
                "providers": ["aws", "gcp"]},
    "results": [ATOMIC_RESULT, COMPOSITE_RESULT],
    "ranked_by": "monthly_usd",
    "unmet_requirements": [],
    "data_quality": {"overall_status": "ok", "per_provider": {}},
}


def _find_keys(obj, key):
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                found.append(v)
            found.extend(_find_keys(v, key))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_find_keys(item, key))
    return found


def _client():
    return TestClient(apimain.app)


def test_compare_strips_store_path_and_adds_ref(monkeypatch):
    monkeypatch.setattr(apimain, "compare", lambda **kw: CANNED_COMPARE)
    body = {"vcpu": 4, "ram_gb": 16, "region": "eu-central", "family": "general-purpose"}

    resp = _client().post("/compare", json=body)
    assert resp.status_code == 200
    data = resp.json()

    # the contract: store_path must not appear anywhere in the wire response
    assert _find_keys(data, "store_path") == []

    # every citation (atomic + each composite constituent) carries a snapshot ref
    refs = _find_keys(data, "snapshot")
    assert len(refs) == 3  # 1 atomic + 2 composite constituents
    aws_ref = data["results"][0]["citation"]["snapshot"]
    assert aws_ref == {
        "provider": "aws",
        "snapshot_iso": "2026-05-27T04-23-36Z",
        "filename": "eu-central-1.json",
    }
    gcp_ref = data["results"][1]["citation"]["composite"][0]["snapshot"]
    assert gcp_ref["provider"] == "gcp" and gcp_ref["filename"] == "skus.json"


def test_lookup_strips_store_path(monkeypatch):
    canned = {
        "request": {"provider": "aws", "instance_type": "a1.xlarge", "region": "eu-central-1"},
        "result": ATOMIC_RESULT,
        "data_quality": {"overall_status": "ok", "per_provider": {}},
        "unmet_requirements": [],
    }
    monkeypatch.setattr(apimain, "lookup", lambda **kw: canned)

    resp = _client().get("/lookup", params={"provider": "aws", "instance_type": "a1.xlarge", "region": "eu-central-1"})
    assert resp.status_code == 200
    data = resp.json()

    assert _find_keys(data, "store_path") == []
    assert data["result"]["citation"]["snapshot"]["filename"] == "eu-central-1.json"


def test_excerpt_rejects_traversal():
    c = _client()
    r = c.get("/citation/excerpt", params={
        "provider": "aws", "snapshot_iso": "../../etc", "filename": "passwd", "path": "$"})
    assert r.status_code == 400


def test_excerpt_rejects_unknown_provider():
    c = _client()
    r = c.get("/citation/excerpt", params={
        "provider": "nope", "snapshot_iso": "x", "filename": "y.json", "path": "$"})
    assert r.status_code == 404


def test_health_lists_providers():
    r = _client().get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert "aws" in r.json()["providers"]
