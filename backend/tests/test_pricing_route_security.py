"""Pricing routes reject provider names outside the supported index set."""

from __future__ import annotations

from fastapi.testclient import TestClient

import api.main as apimain


def test_compare_rejects_unknown_provider():
    client = TestClient(apimain.app)
    response = client.post(
        "/compare",
        json={
            "vcpu": 2,
            "ram_gb": 4,
            "region": "us-east",
            "providers": ["aws", "../store"],
        },
    )

    assert response.status_code == 422


def test_lookup_rejects_unknown_provider():
    client = TestClient(apimain.app)
    response = client.get(
        "/lookup",
        params={
            "provider": "../store",
            "instance_type": "m5.large",
            "region": "us-east-1",
        },
    )

    assert response.status_code == 422
