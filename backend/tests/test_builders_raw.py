"""Every builder reads a raw-first snapshot (provider responses as sent, listed by the
receipt), and every row's citation resolves inside the raw file it names."""

from __future__ import annotations

from typing import Any

import pytest

import ingest._shared as shared
from helpers import write_raw_snapshot
from normalize.indexing import build_provider
from normalize.snapshot_files import SnapshotError, resolve_store_path

ISO = "2026-10-15T08-00-00Z"


@pytest.fixture
def store(monkeypatch, tmp_path):
    root = tmp_path / "store"
    monkeypatch.setattr(shared.ingest_settings, "store_root", str(root))
    return root


def oracle_item(part, family, resource, price):
    metric = "OCPU Per Hour" if resource == "OCPU" else "Gigabyte Per Hour"
    return {
        "partNumber": part,
        "displayName": f"Compute - Standard - {family} - {resource}",
        "metricName": metric,
        "serviceCategory": "Compute - Virtual Machine",
        "currencyCodeLocalizations": [
            {"currencyCode": "EUR", "prices": [{"model": "PAY_AS_YOU_GO", "value": price * 0.9}]},
            {"currencyCode": "USD", "prices": [{"model": "PAY_AS_YOU_GO", "value": price}]},
        ],
    }


def vultr_plan(plan_id, vcpu, ram_mb, monthly):
    return {
        "id": plan_id,
        "vcpu_count": vcpu,
        "ram": ram_mb,
        "monthly_cost": monthly,
        "hourly_cost": round(monthly / 730, 4),
        "type": "vc2",
        "locations": ["ewr", "fra", "sgp"],
        "location_cost": {},
    }


def azure_item(sku, price, meter_id, region="eastus", meter=None):
    return {
        "currencyCode": "USD",
        "retailPrice": price,
        "unitPrice": price,
        "armRegionName": region,
        "meterId": meter_id,
        "meterName": meter or sku.removeprefix("Standard_").replace("_", " "),
        "productName": "Virtual Machines Dsv5 Series",
        "skuName": sku.removeprefix("Standard_"),
        "serviceName": "Virtual Machines",
        "serviceFamily": "Compute",
        "unitOfMeasure": "1 Hour",
        "type": "Consumption",
        "isPrimaryMeterRegion": True,
        "armSkuName": sku,
    }


def gcp_sku(sku_id, description, group, region, nanos, usage="OnDemand"):
    return {
        "skuId": sku_id,
        "description": description,
        "category": {"resourceFamily": "Compute", "resourceGroup": group, "usageType": usage},
        "serviceRegions": [region],
        "pricingInfo": [
            {"pricingExpression": {"tieredRates": [{"unitPrice": {"currencyCode": "USD", "units": "0", "nanos": nanos}}]}}
        ],
    }


def ibm_pricing(plan_name, regions_prices):
    return {
        "count": len(regions_prices),
        "resources": [
            {
                "deployment_region": region,
                "metrics": [
                    {
                        "metric_id": f"part-is.instance-hours-{plan_name}",
                        "amounts": [
                            {"country": "DEU", "currency": "EUR", "prices": [{"quantity_tier": 1, "price": price * 0.9}]},
                            {"country": "USA", "currency": "USD", "prices": [{"quantity_tier": 1, "price": price}]},
                        ],
                    }
                ],
            }
            for region, price in regions_prices
        ],
    }


SNAPSHOTS: dict[str, dict[str, Any]] = {
    "oracle": {
        "products.json": {
            "lastUpdated": "2026-10-08",
            "items": [oracle_item("B1", "E5", "OCPU", 0.03), oracle_item("B2", "E5", "Memory", 0.002)],
        }
    },
    "vultr": {
        "plans-0001.json": {"plans": [vultr_plan("vc2-2c-4gb", 2, 4096, 20.0)], "meta": {"links": {"next": "c", "prev": ""}}},
        "plans-0002.json": {"plans": [vultr_plan("vc2-4c-8gb", 4, 8192, 40.0)], "meta": {"links": {"next": "", "prev": "c"}}},
    },
    "linode": {
        "types-0001.json": {
            "data": [
                {
                    "id": "g6-standard-2",
                    "vcpus": 2,
                    "memory": 4096,
                    "price": {"hourly": 0.036, "monthly": 24.0},
                    "region_prices": [{"id": "br-gru", "hourly": 0.05, "monthly": 33.6}],
                }
            ],
            "page": 1,
            "pages": 1,
            "results": 1,
        }
    },
    "azure": {
        "eastus-0001.json": {"Items": [azure_item("Standard_D4s_v5", 0.192, "m-1")], "NextPageLink": "next"},
        "eastus-0002.json": {
            "Items": [
                azure_item("Standard_D2s_v5", 0.096, "m-2"),
                azure_item("Standard_D2s_v5", 0.02, "m-3", meter="D2s v5 Spot"),
            ],
            "NextPageLink": None,
        },
        "westeurope-0001.json": {"Items": [azure_item("Standard_D4s_v5", 0.21, "m-4", region="westeurope")], "NextPageLink": None},
    },
    "gcp": {
        "skus-0001.json": {
            "skus": [gcp_sku("A-1", "N2 Instance Core running in Virginia", "CPU", "us-east4", 31_611_000)],
            "nextPageToken": "t",
        },
        "skus-0002.json": {
            "skus": [
                gcp_sku("A-2", "N2 Instance Ram running in Virginia", "RAM", "us-east4", 4_237_000),
                gcp_sku("A-3", "Spot Preemptible N2 Instance Core running in Virginia", "CPU", "us-east4", 7_000_000, "Preemptible"),
                gcp_sku("A-4", "Spot Preemptible N2 Instance Ram running in Virginia", "RAM", "us-east4", 900_000, "Preemptible"),
                gcp_sku("A-5", "Preemptible N2 Instance Core running in Virginia", "CPU", "us-east4", 6_000_000, "Preemptible"),
            ],
            "nextPageToken": "",
        },
    },
    "ibm": {
        "catalog-0001.json": {"resources": [{"name": "is.instance", "id": "is.instance"}], "next": None},
        "children-is.instance-0001.json": {"resources": [{"id": "p-1", "name": "bx2-2x8", "kind": "plan"}], "next": None},
        "pricing-p-1.json": ibm_pricing("bx2-2x8", [("us-east", 0.099), ("eu-de", 0.11), ("DEFAULT", 0.1)]),
    },
    "aws": {
        "region_index.json": {"regions": {"us-east-1": {"currentVersionUrl": "/offers/us-east-1/index.json"}}},
        "us-east-1.json": {
            "products": {
                "SKU1": {
                    "productFamily": "Compute Instance",
                    "attributes": {
                        "instanceType": "m5.xlarge",
                        "vcpu": "4",
                        "memory": "16 GiB",
                        "regionCode": "us-east-1",
                        "operatingSystem": "Linux",
                        "tenancy": "Shared",
                        "preInstalledSw": "NA",
                        "capacitystatus": "Used",
                        "licenseModel": "No License required",
                    },
                }
            },
            "terms": {
                "OnDemand": {
                    "SKU1": {"SKU1.OFFER": {"priceDimensions": {"SKU1.OFFER.RATE": {"unit": "Hrs", "pricePerUnit": {"USD": "0.1920000000"}}}}}
                }
            },
        },
    },
}

# Azure and GCP each include their spot rows (one Azure spot meter, a GCP spot CPU
# and RAM rate); GCP's legacy "Preemptible" SKU without the Spot prefix is skipped.
EXPECTED_ROWS = {"oracle": 2, "vultr": 6, "linode": 4, "azure": 4, "gcp": 4, "ibm": 2, "aws": 1}


@pytest.mark.parametrize("provider", sorted(SNAPSHOTS))
def test_builds_from_raw_files_and_citations_resolve(store, provider):
    write_raw_snapshot(store, provider, ISO, SNAPSHOTS[provider])

    result = build_provider(provider)

    report = result.report
    assert result.success, report.human_summary
    assert report.accepted
    assert report.rows_written == EXPECTED_ROWS[provider]
    assert report.missing_fields == {}
    assert report.citation_verification.failed == 0
    assert report.citation_verification.passed == report.rows_written
    assert (store / provider / ISO / "index.parquet").exists()


def test_source_url_is_the_request_that_fetched_the_page(store):
    write_raw_snapshot(store, "gcp", ISO, SNAPSHOTS["gcp"])
    import polars as pl

    build_provider("gcp")
    df = pl.read_parquet(store / "gcp" / ISO / "index.parquet")

    assert set(df["source_url"]) == {
        "https://api.example.test/gcp/skus-0001.json",
        "https://api.example.test/gcp/skus-0002.json",
    }
    assert set(df["store_path"]) == {f"store/gcp/{ISO}/skus-0001.json", f"store/gcp/{ISO}/skus-0002.json"}


def test_a_receipt_from_before_raw_first_is_refused(store):
    snapshot_dir = store / "vultr" / ISO
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "plans.json").write_text('{"plans": []}')
    (snapshot_dir / "receipt.json").write_text('{"success": true, "fetched_at": "2026-05-25T00:00:00Z", "files": []}')

    with pytest.raises(SnapshotError, match="predates raw-first"):
        build_provider("vultr")


def test_a_raw_file_that_does_not_match_its_receipt_is_refused(store):
    snapshot_dir = write_raw_snapshot(store, "oracle", ISO, SNAPSHOTS["oracle"])
    (snapshot_dir / "products.json").write_text("{}")

    with pytest.raises(SnapshotError, match="bytes on disk"):
        build_provider("oracle")


def test_store_paths_resolve_against_the_configured_root(store):
    assert resolve_store_path(f"store/aws/{ISO}/us-east-1.json") == store / "aws" / ISO / "us-east-1.json"
    for bad in ("store/aws/../x/y.json", "aws/x/y.json", "store/aws/x/y/z.json"):
        with pytest.raises(SnapshotError):
            resolve_store_path(bad)


def test_linode_type_without_a_monthly_price_cites_its_hourly_price(store):
    gpu = {
        "id": "g1-gpu-rtx6000-1",
        "vcpus": 8,
        "memory": 32768,
        "price": {"hourly": 1.5, "monthly": None},
        "region_prices": [{"id": "br-gru", "hourly": 1.8, "monthly": None}],
    }
    write_raw_snapshot(store, "linode", ISO, {"types-0001.json": {"data": [gpu], "page": 1, "pages": 1, "results": 1}})
    import polars as pl

    result = build_provider("linode")
    df = pl.read_parquet(store / "linode" / ISO / "index.parquet")

    assert result.success and result.report.missing_fields == {}
    assert set(df["cited_price_kind"]) == {"hourly"}
    assert set(df["json_path"]) == {
        "$.data[?(@.id=='g1-gpu-rtx6000-1')].price.hourly",
        "$.data[?(@.id=='g1-gpu-rtx6000-1')].region_prices[?(@.id=='br-gru')].hourly",
    }
    base = df.filter(pl.col("region_native") != "br-gru")
    assert set(base["monthly_usd"]) == {1.5 * 730}
    assert result.report.citation_verification.failed == 0


def test_spot_rows_are_marked_and_never_mixed_with_on_demand(store):
    import polars as pl

    from normalize.query.service import compare

    for provider in ("azure", "gcp"):
        write_raw_snapshot(store, provider, ISO, SNAPSHOTS[provider])
        build_provider(provider)
    azure = pl.read_parquet(store / "azure" / ISO / "index.parquet")
    gcp = pl.read_parquet(store / "gcp" / ISO / "index.parquet")

    assert azure.filter(pl.col("pricing") == "spot")["instance_type"].to_list() == ["Standard_D2s_v5"]
    assert sorted(gcp.filter(pl.col("pricing") == "spot")["json_path"].to_list()) == [
        "$.skus[?(@.skuId=='A-3')].pricingInfo[0].pricingExpression.tieredRates[0].unitPrice",
        "$.skus[?(@.skuId=='A-4')].pricingInfo[0].pricingExpression.tieredRates[0].unitPrice",
    ]

    on_demand = compare(vcpu=2, ram_gb=8, region="us-east", providers=["azure"])
    spot = compare(vcpu=2, ram_gb=8, region="us-east", providers=["azure"], pricing="spot")
    assert on_demand["results"][0]["hourly_usd"] == 0.096
    assert spot["results"][0]["hourly_usd"] == 0.02
    assert spot["request"]["pricing"] == "spot"


def test_a_spot_request_names_providers_without_spot_prices(store):
    from normalize.query.service import compare

    for provider in ("azure", "vultr"):
        write_raw_snapshot(store, provider, ISO, SNAPSHOTS[provider])
        build_provider(provider)

    spot = compare(vcpu=2, ram_gb=4, region="us-east", providers=["azure", "vultr"], pricing="spot")

    assert spot["unmet_requirements"] == [{"provider": "vultr", "reason": "no spot prices indexed"}]
