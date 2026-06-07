"""Pricing routes reject provider names outside the supported index set."""

from __future__ import annotations

import inspect
from typing import Annotated, get_args, get_origin, get_type_hints

from fastapi.testclient import TestClient

import agent.tools.pricing as pricing_tools
import api.main as apimain
import api.routes.pricing as pricing_routes
from app_config import settings
from normalize.query.inputs import CompareQueryArgs, LookupQueryArgs


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


def test_compare_rejects_extra_fields():
    client = TestClient(apimain.app)
    response = client.post(
        "/compare",
        json={
            "vcpu": 2,
            "ram_gb": 4,
            "region": "us-east",
            "internal": "leak",
        },
    )

    assert response.status_code == 422


def test_compare_rejects_invalid_family_and_expand():
    client = TestClient(apimain.app)
    response = client.post(
        "/compare",
        json={
            "vcpu": 2,
            "ram_gb": 4,
            "region": "us-east",
            "family": "gpu",
            "expand": "everything",
        },
    )

    assert response.status_code == 422


def test_compare_rejects_oversized_shape():
    client = TestClient(apimain.app)
    response = client.post(
        "/compare",
        json={
            "vcpu": 2048,
            "ram_gb": 9000,
            "region": "us-east",
        },
    )

    assert response.status_code == 422


def test_compare_rejects_path_like_region():
    client = TestClient(apimain.app)
    response = client.post(
        "/compare",
        json={
            "vcpu": 2,
            "ram_gb": 4,
            "region": "../store",
        },
    )

    assert response.status_code == 422


def test_compare_rejects_oversized_body(monkeypatch):
    monkeypatch.setattr(settings, "public_max_body_bytes", 64)
    client = TestClient(apimain.app)
    response = client.post(
        "/compare",
        json={
            "vcpu": 2,
            "ram_gb": 4,
            "region": "us-east",
            "padding": "x" * 256,
        },
    )

    assert response.status_code == 413
    assert response.json()["error"] == "public_body_too_large"


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


def test_lookup_rejects_path_like_selector():
    client = TestClient(apimain.app)
    response = client.get(
        "/lookup",
        params={
            "provider": "aws",
            "instance_type": "../m5.large",
            "region": "us-east-1",
        },
    )

    assert response.status_code == 422


def test_lookup_rejects_overlong_query_values():
    client = TestClient(apimain.app)
    response = client.get(
        "/lookup",
        params={
            "provider": "aws",
            "instance_type": "x" * 129,
            "region": "us-east-1",
        },
    )

    assert response.status_code == 422


def test_public_route_and_agent_tool_share_compare_contract():
    req_hints = get_type_hints(pricing_routes.post_compare, include_extras=True)
    lookup_hints = get_type_hints(pricing_routes.get_lookup, include_extras=True)
    lookup_param = inspect.signature(pricing_routes.get_lookup).parameters["req"]

    assert req_hints["req"] is CompareQueryArgs
    assert issubclass(pricing_tools.CompareToolArgs, CompareQueryArgs)
    assert lookup_param.name == "req"
    assert get_origin(lookup_hints["req"]) is Annotated
    assert get_args(lookup_hints["req"])[0] is LookupQueryArgs
