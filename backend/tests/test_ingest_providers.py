"""Each provider's fetch against a fake API: which requests it makes, how it pages,
and that every response is stored as sent."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs

import httpx
import pytest

import ingest._shared as shared
from ingest import aws, azure, gcp, linode, oracle, vultr
from ingest._shared import IngestError
from ingest.ibm import catalog as ibm
from ingest.raw_snapshot import RawSnapshot


@pytest.fixture
def store(monkeypatch, tmp_path):
    root = tmp_path / "store"
    monkeypatch.setattr(shared.ingest_settings, "store_root", str(root))
    monkeypatch.setattr(shared.ingest_settings, "http_initial_backoff_seconds", 0.0)
    monkeypatch.setattr(shared.ingest_settings, "http_max_retries", 0)
    monkeypatch.setattr(shared.ingest_settings, "min_request_interval_seconds", 0.0)
    return root


def run(module_fetch, provider: str, handler) -> RawSnapshot:
    snapshot = RawSnapshot.start(provider)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await module_fetch(snapshot, client)

    asyncio.run(go())
    return snapshot


def body(obj) -> httpx.Response:
    return httpx.Response(200, content=json.dumps(obj).encode())


def query(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}


def test_oracle_stores_the_response_unchanged(store):
    raw = b'{"lastUpdated": "2026-10-08", "items": [], "newTopLevelField": {"kept": true}}'
    snapshot = run(oracle.fetch, "oracle", lambda r: httpx.Response(200, content=raw))

    assert snapshot.path("products.json").read_bytes() == raw


def test_vultr_follows_the_cursor_until_it_is_empty(store):
    def handler(request):
        cursor = query(request).get("cursor")
        assert query(request)["per_page"] == "500"
        if cursor is None:
            return body({"plans": [{"id": "a"}], "meta": {"total": 2, "links": {"next": "c2", "prev": ""}}})
        assert cursor == "c2"
        return body({"plans": [{"id": "b"}], "meta": {"total": 2, "links": {"next": "", "prev": "c1"}}})

    snapshot = run(vultr.fetch, "vultr", handler)

    assert snapshot.done("plans-0001.json") and snapshot.done("plans-0002.json")
    assert snapshot.done("plans-0003.json") is None


def test_vultr_fails_loud_when_paging_shape_changes(store):
    with pytest.raises(IngestError, match="paging shape"):
        run(vultr.fetch, "vultr", lambda r: body({"plans": []}))


def test_linode_reads_every_page(store):
    def handler(request):
        page = int(query(request)["page"])
        return body({"data": [{"id": f"t{page}"}], "page": page, "pages": 2, "results": 2})

    snapshot = run(linode.fetch, "linode", handler)

    assert snapshot.read_json("types-0002.json")["data"] == [{"id": "t2"}]


def test_aws_fetches_index_then_each_region(store):
    def handler(request):
        if request.url.path.endswith("region_index.json"):
            return body({"regions": {r: {"currentVersionUrl": f"/offers/{r}/index.json"} for r in aws.REGIONS}})
        return httpx.Response(200, content=f'{{"region": "{request.url.path}"}}'.encode())

    snapshot = run(aws.fetch, "aws", handler)

    assert sorted(f for f in ["region_index.json", *(f"{r}.json" for r in aws.REGIONS)] if snapshot.done(f)) == sorted(
        ["region_index.json", *(f"{r}.json" for r in aws.REGIONS)]
    )


def test_aws_refuses_a_missing_region(store):
    with pytest.raises(IngestError, match="not in region_index"):
        run(aws.fetch, "aws", lambda r: body({"regions": {}}))


def test_azure_pages_each_region_and_resumes_mid_region(store):
    pages = {region: 2 for region in azure.REGIONS}
    fail_once = {"westeurope-2": True}

    def handler(request):
        url = str(request.url)
        region = next(r for r in azure.REGIONS if r in url) if "page=" not in url else url.split("region=")[1].split("&")[0]
        n = int(url.split("page=")[1].split("&")[0]) if "page=" in url else 1
        if fail_once.pop(f"{region}-{n}", False):
            raise httpx.ConnectError("reset")
        nxt = f"https://prices.azure.com/api/retail/prices?region={region}&page={n + 1}" if n < pages[region] else None
        return body({"Items": [{"armRegionName": region, "n": n}], "NextPageLink": nxt})

    with pytest.raises(httpx.ConnectError):
        run(azure.fetch, "azure", handler)

    calls: list[str] = []

    def counting(request):
        calls.append(str(request.url))
        return handler(request)

    snapshot = run(azure.fetch, "azure", counting)

    assert snapshot.resumed
    assert len(calls) == 3  # westeurope page 2, then southeastasia pages 1 and 2
    assert all(snapshot.done(azure.page_name(r, n)) for r in azure.REGIONS for n in (1, 2))


def test_gcp_follows_page_tokens(store):
    def handler(request):
        token = query(request).get("pageToken")
        if token is None:
            return body({"skus": [{"skuId": "1"}], "nextPageToken": "t2"})
        return body({"skus": [{"skuId": "2"}], "nextPageToken": ""})

    snapshot = run(gcp.fetch, "gcp", handler)

    assert snapshot.read_json("skus-0002.json")["skus"] == [{"skuId": "2"}]


def test_ibm_walks_catalog_children_and_pricing(store):
    def handler(request):
        path = request.url.path
        if path.endswith("/api/v1") and "offset" not in str(request.url):
            services = [{"name": name, "id": name} for name in ibm.COMPUTE_SERVICE_NAMES]
            return body({"resources": services[:1], "next": "https://globalcatalog.cloud.ibm.com/api/v1?q=kind:service&offset=1"})
        if path.endswith("/api/v1"):
            services = [{"name": name, "id": name} for name in ibm.COMPUTE_SERVICE_NAMES]
            return body({"resources": services[1:], "next": None})
        if path.endswith("/pricing/deployment"):
            return body({"resources": [{"deployment_region": "us-east"}]})
        service = path.split("/")[-2]
        return body({"resources": [{"id": f"{service}-plan", "name": "bx2-2x8"}], "next": None})

    snapshot = run(ibm.fetch, "ibm", handler)

    assert snapshot.done("catalog-0002.json")
    for name in ibm.COMPUTE_SERVICE_NAMES:
        assert snapshot.done(f"children-{name}-0001.json")
        assert snapshot.done(f"pricing-{name}-plan.json")


def test_ibm_fails_loud_when_a_compute_service_is_gone(store):
    with pytest.raises(IngestError, match="not in the IBM catalog"):
        run(ibm.fetch, "ibm", lambda r: body({"resources": [], "next": None}))


def test_gcp_stops_when_the_provider_repeats_a_page_token(store):
    calls: list[str] = []

    def handler(request):
        calls.append(str(request.url))
        return body({"skus": [], "nextPageToken": "same"})

    with pytest.raises(IngestError, match="same next page twice"):
        run(gcp.fetch, "gcp", handler)
    assert len(calls) == 2


def test_azure_stops_when_next_links_never_end(store, monkeypatch):
    monkeypatch.setattr(shared.ingest_settings, "max_pages", 5)
    calls: list[str] = []

    def handler(request):
        calls.append(str(request.url))
        return body({"Items": [], "NextPageLink": f"https://prices.azure.com/api/retail/prices?page={len(calls) + 1}"})

    with pytest.raises(IngestError, match="more than 5 pages"):
        run(azure.fetch, "azure", handler)
    assert len(calls) == 5


def test_ibm_fetches_a_plan_listed_twice_once(store):
    pricing_calls: list[str] = []

    def handler(request):
        path = request.url.path
        if path.endswith("/api/v1"):
            return body({"resources": [{"name": n, "id": n} for n in ibm.COMPUTE_SERVICE_NAMES], "next": None})
        if path.endswith("/pricing/deployment"):
            pricing_calls.append(path)
            return body({"resources": []})
        return body({"resources": [{"id": "dup"}, {"id": "dup"}], "next": None})

    run(ibm.fetch, "ibm", handler)

    assert pricing_calls.count("/api/v1/dup/pricing/deployment") == 1


def test_oracle_refuses_a_200_that_is_not_json(store):
    with pytest.raises(IngestError, match="not JSON"):
        run(oracle.fetch, "oracle", lambda r: httpx.Response(200, content=b"oops"))


def test_gcp_key_is_trimmed_and_checked(monkeypatch, capsys):
    monkeypatch.setattr(gcp, "load_dotenv_if_present", lambda: None)
    monkeypatch.setenv("GCP_API_KEY", "AIzaGoodKey_123-x \n")
    assert gcp.api_key() == "AIzaGoodKey_123-x"

    monkeypatch.setenv("GCP_API_KEY", "AIza bad key")
    with pytest.raises(SystemExit):
        gcp.api_key()
    assert "AIza bad key" not in capsys.readouterr().out
