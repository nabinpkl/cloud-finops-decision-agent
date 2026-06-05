"""Mocked integration tests for normalize.query.service.

The two disk seams compare()/lookup() touch are patched:
  - normalize.query.service.load_latest  -> returns an in-memory (df, snapshot_dir)
  - normalize.query.service.compute_envelope -> stubbed (its own logic is tested in
    test_integration_data_quality.py)

_load_flex_rules() is left real: the synthesis test deliberately uses the real
gcp.n2 rule so a change to those bounds is caught here.
"""

from __future__ import annotations

import normalize.query.service as q
from helpers import instance_row, make_df, rate_row, write_receipt

HOURS_PER_MONTH = 730.0


def _patch_loader(monkeypatch, tmp_path, df):
    snapshot_dir = tmp_path / "snap"
    write_receipt(snapshot_dir, fetched_at="2026-05-27T00:00:00Z")

    def fake_load_latest(provider):
        return df, snapshot_dir

    monkeypatch.setattr(q, "load_latest", fake_load_latest)
    monkeypatch.setattr(q, "compute_envelope", lambda providers: {"overall_status": "ok", "per_provider": {}})


def test_closest_larger_picks_smallest_fit(monkeypatch, tmp_path):
    df = make_df([
        instance_row(provider="aws", instance_type="m5.large", family="general-purpose",
                     region_canonical="us-east", vcpu=2, ram_gb=8, hourly_usd=0.096, monthly_usd=70.0),
        instance_row(provider="aws", instance_type="a1.xlarge", family="general-purpose",
                     region_canonical="us-east", vcpu=4, ram_gb=8, hourly_usd=0.115, monthly_usd=84.0),
        instance_row(provider="aws", instance_type="m5.xlarge", family="general-purpose",
                     region_canonical="us-east", vcpu=4, ram_gb=16, hourly_usd=0.192, monthly_usd=140.0),
    ])
    _patch_loader(monkeypatch, tmp_path, df)

    out = q.compare(vcpu=4, ram_gb=8, region="us-east", family="general-purpose", providers=["aws"])

    assert len(out["results"]) == 1
    winner = out["results"][0]
    # m5.large fails the vcpu>=4 filter; a1.xlarge (4,8) beats m5.xlarge (4,16)
    # because closest-larger sorts by vcpu, then ram, before price.
    assert winner["instance_type"] == "a1.xlarge"
    assert winner["vcpu_actual"] == 4
    assert winner["ram_gb_actual"] == 8.0
    assert winner["considered_count"] == 2  # only the two that met the spec


def test_unmet_when_nothing_fits(monkeypatch, tmp_path):
    df = make_df([
        instance_row(provider="aws", instance_type="t3.micro", family="general-purpose",
                     region_canonical="us-east", vcpu=2, ram_gb=1, hourly_usd=0.01, monthly_usd=7.0),
    ])
    _patch_loader(monkeypatch, tmp_path, df)

    out = q.compare(vcpu=4, ram_gb=8, region="us-east", family="general-purpose", providers=["aws"])

    assert out["results"] == []
    assert out["unmet_requirements"] == [
        {"provider": "aws", "reason": "no candidate meets vcpu and ram_gb"}
    ]


def test_rate_synthesis_math_and_composite_citation(monkeypatch, tmp_path):
    cpu_rate = 0.02
    ram_rate = 0.005
    df = make_df([
        rate_row(provider="gcp", flex_family="n2", resource="cpu", family="general-purpose",
                 region_canonical="us-east", rate_unit="per_vcpu_hour", hourly_usd=cpu_rate),
        rate_row(provider="gcp", flex_family="n2", resource="ram", family="general-purpose",
                 region_canonical="us-east", rate_unit="per_gb_ram_hour", hourly_usd=ram_rate),
    ])
    _patch_loader(monkeypatch, tmp_path, df)

    out = q.compare(vcpu=4, ram_gb=16, region="us-east", family="general-purpose", providers=["gcp"])

    assert len(out["results"]) == 1
    r = out["results"][0]
    assert r["synthesized"] is True
    # n2 has vcpu_per_unit=1, so compute_quantity == vcpu == 4.
    expected_hourly = 4 * cpu_rate + 16 * ram_rate
    assert abs(r["hourly_usd"] - expected_hourly) < 1e-9
    assert abs(r["monthly_usd"] - expected_hourly * HOURS_PER_MONTH) < 1e-6

    composite = r["citation"]["composite"]
    assert len(composite) == 2
    assert sum(c["contribution_usd"] for c in composite) == r["hourly_usd"]
    units = {c["rate_unit"] for c in composite}
    assert units == {"per_vcpu_hour", "per_gb_ram_hour"}


def test_rate_synthesis_rejects_out_of_range_ratio(monkeypatch, tmp_path):
    # n2 ram_per_unit_gb max is 8.0; ask 4 vCPU 64 GB -> ratio 16, invalid.
    df = make_df([
        rate_row(provider="gcp", flex_family="n2", resource="cpu", family="general-purpose",
                 region_canonical="us-east", rate_unit="per_vcpu_hour", hourly_usd=0.02),
        rate_row(provider="gcp", flex_family="n2", resource="ram", family="general-purpose",
                 region_canonical="us-east", rate_unit="per_gb_ram_hour", hourly_usd=0.005),
    ])
    _patch_loader(monkeypatch, tmp_path, df)

    out = q.compare(vcpu=4, ram_gb=64, region="us-east", family="general-purpose", providers=["gcp"])

    assert out["results"] == []


def test_cheapest_wins_across_providers(monkeypatch, tmp_path):
    df = make_df([
        instance_row(provider="aws", instance_type="a1.xlarge", family="general-purpose",
                     region_canonical="us-east", vcpu=4, ram_gb=8, hourly_usd=0.115, monthly_usd=84.0),
        instance_row(provider="vultr", instance_type="vc2-4c-8gb", family="general-purpose",
                     region_canonical="us-east", vcpu=4, ram_gb=8, hourly_usd=0.055, monthly_usd=40.0),
    ])

    snapshot_dir = tmp_path / "snap"
    write_receipt(snapshot_dir, fetched_at="2026-05-27T00:00:00Z")
    monkeypatch.setattr(q, "load_latest", lambda provider: (df, snapshot_dir))
    monkeypatch.setattr(q, "compute_envelope", lambda providers: {"overall_status": "ok", "per_provider": {}})

    out = q.compare(vcpu=4, ram_gb=8, region="us-east", family="general-purpose",
                    providers=["aws", "vultr"])

    assert [r["provider"] for r in out["results"]] == ["vultr", "aws"]
    assert out["ranked_by"] == "monthly_usd"
