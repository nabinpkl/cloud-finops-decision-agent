"""Rows keep the currency the provider published, and GPU types never answer a CPU
question (ADR 0019)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

import ingest._shared as shared
from helpers import iso_hours_ago, make_df
from normalize.indexing.verifier import verify
from normalize.query.loader import load_index
from normalize.query.service import compare, lookup
from normalize.schema import IndexRow

ISO = "2026-10-17T08-00-00Z"


@pytest.fixture
def store(monkeypatch, tmp_path):
    root = tmp_path / "store"
    monkeypatch.setattr(shared.ingest_settings, "store_root", str(root))
    load_index.cache_clear()
    yield root
    load_index.cache_clear()


def row(instance_type: str, **overrides: Any) -> IndexRow:
    fields: dict[str, Any] = {
        "provider": "vultr",
        "snapshot_iso": ISO,
        "instance_type": instance_type,
        "family": "general-purpose",
        "region_native": "par1",
        "region_canonical": "eu-central",
        "vcpu": 4,
        "ram_gb": 16.0,
        "hourly_usd": 0.1,
        "monthly_usd": 73.0,
        "source_url": "https://example.test/vultr",
        "store_path": f"store/vultr/{ISO}/prices.json",
        "json_path": f"$.{instance_type}",
        "cited_price_kind": "monthly",
    }
    fields.update(overrides)
    return IndexRow(**fields)


def eur(instance_type: str, **overrides: Any) -> IndexRow:
    fields: dict[str, Any] = {
        "hourly_usd": None,
        "monthly_usd": None,
        "hourly_eur": 0.05,
        "monthly_eur": 36.5,
        "cited_price_kind": "hourly_eur",
    }
    fields.update(overrides)
    return row(instance_type, **fields)


def serve(store, provider: str, rows: list[IndexRow]) -> None:
    snapshot_dir = store / provider / ISO
    snapshot_dir.mkdir(parents=True)
    make_df(rows).write_parquet(snapshot_dir / "index.parquet")
    (snapshot_dir / "receipt.json").write_text(json.dumps({"fetched_at": iso_hours_ago(1)}))


def test_a_row_carries_one_currency():
    with pytest.raises(ValidationError, match="one currency"):
        row("both", hourly_eur=0.09)


def test_a_row_must_have_the_price_it_cites():
    with pytest.raises(ValidationError, match="cites hourly_eur"):
        row("cites-eur-without-it", cited_price_kind="hourly_eur")


def test_the_verifier_applies_the_cited_scale(store):
    raw = store / "vultr" / ISO / "prices.json"
    raw.parent.mkdir(parents=True)
    raw.write_text(json.dumps({"b3-8": 6050000, "per-minute": 0.5}))
    scaled = row("b3-8", hourly_usd=0.0605, monthly_usd=None, cited_price_kind="hourly", cited_price_scale=1e-8)
    per_minute = eur("per-minute", hourly_eur=30.0, cited_price_scale=60.0)
    wrong = row("b3-8", hourly_usd=6050000 * 1e-6, monthly_usd=None, cited_price_kind="hourly", cited_price_scale=1e-6 * 2)

    assert verify([scaled, per_minute]).failed == 0
    assert verify([wrong]).failed == 1


def test_gpu_types_never_answer_a_cpu_question(store):
    serve(
        store,
        "vultr",
        [
            row("old-gpu", family="gpu", hourly_usd=0.01, monthly_usd=7.3),
            row("labelled-gpu", family="unclassified", gpu_model="L4", gpu_count=1, gpu_memory_gb=24.0, monthly_usd=8.0),
            row("cpu-box", monthly_usd=73.0),
        ],
    )

    result = compare(vcpu=4, ram_gb=16, region="eu-central", providers=["vultr"])

    assert [r["instance_type"] for r in result["results"]] == ["cpu-box"]
    assert result["results"][0]["considered_count"] == 1


def test_eur_rows_are_never_ranked_against_usd(store):
    serve(store, "vultr", [eur("cheap-in-eur"), row("dearer-in-usd", monthly_usd=99.0)])
    serve(store, "linode", [eur("only-eur", provider="linode", store_path=f"store/linode/{ISO}/prices.json")])

    result = compare(vcpu=4, ram_gb=16, region="eu-central", providers=["vultr", "linode"])

    assert [r["instance_type"] for r in result["results"]] == ["dearer-in-usd"]
    assert result["unmet_requirements"] == [
        {"provider": "linode", "reason": "only EUR prices match; not ranked against USD prices"}
    ]


def test_lookup_names_why_it_cannot_serve_an_eur_or_gpu_priced_type(store):
    serve(
        store,
        "vultr",
        [
            eur("eur-type"),
            row("H100 (secure)", family="gpu", vcpu=None, ram_gb=None, gpu_model="H100", gpu_count=1, monthly_usd=None, hourly_usd=2.0, cited_price_kind="hourly"),
        ],
    )

    eur_answer = lookup(provider="vultr", instance_type="eur-type", region="eu-central")
    gpu_answer = lookup(provider="vultr", instance_type="H100 (secure)", region="eu-central")

    assert eur_answer["result"] is None
    assert eur_answer["unmet_requirements"][0]["reason"] == "eur-type is priced in EUR only; lookup answers in USD"
    assert gpu_answer["result"] is None
    assert "priced per GPU" in gpu_answer["unmet_requirements"][0]["reason"]


def test_a_scaled_citation_carries_its_scale(store):
    serve(store, "vultr", [row("b3-8", hourly_usd=0.0605, monthly_usd=None, cited_price_kind="hourly", cited_price_scale=1e-8)])

    citation = compare(vcpu=4, ram_gb=16, region="eu-central", providers=["vultr"])["results"][0]["citation"]

    assert citation["price_scale"] == 1e-8
