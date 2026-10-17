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


def scaleway_product(offer, zone, nanos, *, unit="hour", status="general_availability", gpu=None, vcpu=4, ram_gib=16):
    hardware = {"cpu": {"virtual": {"count": vcpu}}, "ram": {"size": ram_gib * 1024**3}}
    if gpu:
        hardware["gpu"] = gpu
    return {
        "sku": f"/instance/server/{offer.lower().replace('-', '_')}/{zone}",
        "product": offer,
        "locality": {"zone": zone},
        "price": {"retail_price": {"currency_code": "EUR", "units": 0, "nanos": nanos}},
        "properties": {"hardware": hardware, "instance": {"offer_id": offer}},
        "unit_of_measure": {"unit": unit, "size": 1},
        "status": status,
    }


def ovh_addon(plan_code, price, *, regions=None, cores=2, memory=8, os="linux", gpu=None):
    technical = {"cpu": {"cores": cores}, "memory": {"size": memory}, "os": {"family": os}}
    if gpu:
        technical["gpu"] = gpu
    return {
        "planCode": plan_code,
        "invoiceName": plan_code.split(".")[0],
        "product": "publiccloud-instance",
        "pricings": [{"description": "hourly prices", "price": price}],
        "configurations": [{"name": "region", "values": regions}] if regions else [],
        "blobs": {"technical": technical},
    }


def cherry_plan(slug, threads, ram_gb, prices, regions):
    return {
        "slug": slug,
        "type": "vps",
        "specs": {"cpus": {"threads": threads}, "memory": {"total": ram_gb, "unit": "GB"}},
        "pricing": [{"unit": unit, "price": price, "currency": "USD"} for unit, price in prices.items()],
        "available_regions": [{"slug": r} for r in regions],
    }


def runpod_gpu(gpu_id, *, secure=None, community=None, memory=80):
    return {
        "id": gpu_id,
        "displayName": gpu_id.split()[-1],
        "memoryInGb": memory,
        "secureCloud": secure is not None,
        "communityCloud": community is not None,
        "securePrice": secure if secure is not None else 0.5,
        "communityPrice": community if community is not None else 0.5,
        "secureSpotPrice": None,
        "communitySpotPrice": None,
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
    "scaleway": {
        "products-0001.json": {
            "products": [
                scaleway_product("POP2-4C-16G", "fr-par-1", 120_000_000),
                scaleway_product("POP2-4C-16G", "nl-ams-1", 125_000_000),
                scaleway_product("PLAY2-NANO", "fr-par-1", 10_000_000, status="retired"),
                scaleway_product("POP2-4C-16G-WIN", "fr-par-1", 300_000_000),
                scaleway_product(
                    "H100-SXM-2-80G", "fr-par-2", 110_330_000, unit="minute",
                    gpu={"count": 2, "type": "H100-SXM"}, vcpu=32, ram_gib=240,
                ),
                {"sku": "/instance/snapshot/l_ssd/fr-par-1", "status": "general_availability"},
            ],
            "total_count": 6,
        }
    },
    "ovh": {
        "catalog-us.json": {
            "locale": {"currencyCode": "USD", "subsidiary": "US"},
            "addons": [
                ovh_addon("b3-8.consumption", 6_050_000, regions=["US-EAST-VA-1", "US-WEST-OR-1"]),
                ovh_addon("b3-8.monthly.postpaid", 2_200_000_000, regions=["US-EAST-VA-1"]),
                ovh_addon("win-b3-8.consumption", 9_000_000, regions=["US-EAST-VA-1"], os="windows"),
                ovh_addon("b3-16.consumption.US-EAST-LZ-DAL-A", 12_000_000),
                ovh_addon("l4-90.consumption", 90_000_000, cores=22, memory=90, gpu={"model": "L4", "number": 1, "memory": {"size": 24}}),
            ],
        },
        "catalog-fr.json": {
            "locale": {"currencyCode": "EUR", "subsidiary": "FR"},
            "addons": [ovh_addon("b3-8.consumption", 5_120_000, regions=["DE1"])],
        },
    },
    "exoscale": {
        "pricing-opencompute.json": {
            "usd": {"running_medium": "0.04666000", "running_cpu_extra_large": "0.20000000", "running_gpua30_small": "1.20000000"},
            "eur": {"running_medium": "0.04000000"},
        },
        "instance-types.json": {
            "instance-types": [
                {"family": "standard", "size": "medium", "cpus": 2, "memory": 4 * 1024**3, "zones": ["de-fra-1", "ch-gva-2"]},
                {"family": "gpua30", "size": "small", "cpus": 12, "memory": 56 * 1024**3, "gpus": 1, "zones": ["de-fra-1"]},
                {"family": "cpu", "size": "extra-large", "cpus": 8, "memory": 16 * 1024**3, "zones": ["de-fra-1"]},
            ]
        },
    },
    "cherryservers": {
        "plans.json": [
            cherry_plan("B1-2-2gb-40s-shared", 2, 2, {"Hourly": 0.03, "Monthly": 18.0}, ["DE-Frankfurt", "US-Chicago"]),
            cherry_plan("e3-1240v3", 8, 32, {"Hourly": 0.1, "Monthly": 55.0, "Spot hourly": 0.07}, ["LT-Siauliai"]),
        ]
    },
    "runpod": {
        "gpu-types.json": {
            "data": {
                "gpuTypes": [
                    runpod_gpu("NVIDIA H100 80GB HBM3", secure=3.99, community=2.69),
                    runpod_gpu("NVIDIA A40", secure=0.59, memory=48),
                    runpod_gpu("unknown"),
                ]
            }
        }
    },
}

# Azure and GCP each include their spot rows (one Azure spot meter, a GCP spot CPU
# and RAM rate); GCP's legacy "Preemptible" SKU without the Spot prefix is skipped.
# Scaleway, OVHcloud and RunPod skip retired, Windows, monthly-commitment, Local
# Zone and unoffered-tier entries; Cherry Servers adds a spot row for its spot plan.
EXPECTED_ROWS = {
    "oracle": 2, "vultr": 6, "linode": 4, "azure": 4, "gcp": 4, "ibm": 2, "aws": 1,
    "scaleway": 3, "ovh": 4, "exoscale": 4, "cherryservers": 4, "runpod": 3,
}


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


def test_ovh_keeps_each_catalog_in_its_currency_and_unit(store):
    import polars as pl

    write_raw_snapshot(store, "ovh", ISO, SNAPSHOTS["ovh"])
    build_provider("ovh")
    df = pl.read_parquet(store / "ovh" / ISO / "index.parquet")

    us = df.filter(pl.col("region_native") == "US-EAST-VA-1").row(0, named=True)
    de = df.filter(pl.col("region_native") == "DE1").row(0, named=True)
    gpu = df.filter(pl.col("instance_type") == "l4-90").row(0, named=True)
    assert (us["hourly_usd"], us["hourly_eur"], us["region_canonical"]) == (6_050_000 * 1e-8, None, "us-east")
    assert (de["hourly_usd"], de["hourly_eur"], de["cited_price_kind"]) == (None, 5_120_000 * 1e-8, "hourly_eur")
    assert set(df["cited_price_scale"]) == {1e-8}
    assert (gpu["region_native"], gpu["region_canonical"], gpu["gpu_model"], gpu["gpu_count"]) == ("global", None, "L4", 1)


def test_scaleway_prices_stay_eur_and_per_minute_offers_scale_to_hourly(store):
    import polars as pl

    write_raw_snapshot(store, "scaleway", ISO, SNAPSHOTS["scaleway"])
    build_provider("scaleway")
    df = pl.read_parquet(store / "scaleway" / ISO / "index.parquet")

    h100 = df.filter(pl.col("instance_type") == "H100-SXM-2-80G").row(0, named=True)
    assert df["hourly_usd"].is_null().all()
    assert h100["hourly_eur"] == pytest.approx(0.11033 * 60)
    assert (h100["cited_price_scale"], h100["gpu_count"], h100["gpu_model"], h100["gpu_memory_gb"]) == (60.0, 2, "H100-SXM", 80.0)
    assert h100["family"] == "gpu"
    assert set(df.filter(pl.col("instance_type") == "POP2-4C-16G")["region_native"]) == {"fr-par-1", "nl-ams-1"}


def test_cherry_spot_plans_get_a_spot_row(store):
    from normalize.query.service import compare

    write_raw_snapshot(store, "cherryservers", ISO, SNAPSHOTS["cherryservers"])
    build_provider("cherryservers")

    spot = compare(vcpu=8, ram_gb=32, region="LT-Siauliai", providers=["cherryservers"], pricing="spot")
    on_demand = compare(vcpu=2, ram_gb=2, region="eu-central", providers=["cherryservers"])

    assert spot["results"][0]["hourly_usd"] == 0.07
    assert on_demand["results"][0]["instance_type"] == "B1-2-2gb-40s-shared"
    assert on_demand["results"][0]["monthly_usd"] == 18.0


def test_compare_says_why_eur_and_gpu_only_providers_have_no_result(store):
    from normalize.query.service import compare

    for provider in ("scaleway", "runpod", "exoscale"):
        write_raw_snapshot(store, provider, ISO, SNAPSHOTS[provider])
        build_provider(provider)

    result = compare(vcpu=2, ram_gb=4, region="eu-central", providers=["scaleway", "runpod", "exoscale"])

    assert [(r["provider"], r["instance_type"]) for r in result["results"]] == [("exoscale", "standard.medium")]
    assert result["unmet_requirements"] == [
        {"provider": "scaleway", "reason": "only EUR prices match; not ranked against USD prices"},
        {"provider": "runpod", "reason": "prices GPU types only; not a vCPU and RAM match"},
    ]


def test_compare_names_a_provider_with_no_prices_in_the_region(store):
    from normalize.query.service import compare

    write_raw_snapshot(store, "exoscale", ISO, SNAPSHOTS["exoscale"])
    build_provider("exoscale")

    result = compare(vcpu=2, ram_gb=4, region="us-east", providers=["exoscale"])

    assert result["unmet_requirements"] == [{"provider": "exoscale", "reason": "no prices indexed in us-east"}]
