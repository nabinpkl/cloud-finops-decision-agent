"""IBM Global Catalog and deployment-pricing walk, stored raw.

Three hops, each response its own file:
  1. catalog pages (`catalog-NNNN.json`): every service, paged by `next`;
  2. per compute service, its child plans (`children-<service>-NNNN.json`), paged by `next`;
  3. per plan, `/{plan_id}/pricing/deployment` (`pricing-<plan_id>.json`), which
     carries that plan's price in every region it is deployed to.
"""

from __future__ import annotations

import asyncio

import httpx

from ingest._shared import IngestError
from ingest.config import ingest_settings
from ingest.ibm.constants import (
    CATALOG_BASE,
    CATALOG_FIRST_URL,
    COMPUTE_SERVICE_NAMES,
    PRICING_CONCURRENCY,
)
from ingest.raw_snapshot import RawSnapshot


def catalog_page_name(n: int) -> str:
    return f"catalog-{n:04d}.json"


def children_page_name(service: str, n: int) -> str:
    return f"children-{service}-{n:04d}.json"


def pricing_name(plan_id: str) -> str:
    return f"pricing-{plan_id}.json"


async def fetch(snapshot: RawSnapshot, client: httpx.AsyncClient) -> None:
    services = await _paged_resources(snapshot, client, CATALOG_FIRST_URL, catalog_page_name)
    by_name = {name: service for service in services if (name := service.get("name"))}

    sem = asyncio.Semaphore(PRICING_CONCURRENCY)
    for service_name in COMPUTE_SERVICE_NAMES:
        service = by_name.get(service_name)
        if service is None or not service.get("id"):
            raise IngestError(f"compute service {service_name!r} is not in the IBM catalog (or has no id)")
        plans = await _paged_resources(
            snapshot,
            client,
            f"{CATALOG_BASE}/{service['id']}/%2A",
            lambda n, s=service_name: children_page_name(s, n),
        )
        plan_ids = [plan_id for plan in plans if (plan_id := plan.get("id"))]
        await asyncio.gather(*(_pricing(snapshot, client, plan_id, sem) for plan_id in plan_ids))


async def _paged_resources(snapshot: RawSnapshot, client: httpx.AsyncClient, first_url: str, name_for) -> list[dict]:
    resources: list[dict] = []
    url: str | None = first_url
    n = 1
    while url:
        name = name_for(n)
        await snapshot.fetch(client, name, url, timeout=ingest_settings.http_timeout_seconds)
        page = snapshot.read_json(name)
        resources.extend(page.get("resources", []))
        url = page.get("next") or None
        n += 1
    return resources


async def _pricing(snapshot: RawSnapshot, client: httpx.AsyncClient, plan_id: str, sem: asyncio.Semaphore) -> None:
    async with sem:
        await snapshot.fetch(
            client,
            pricing_name(plan_id),
            f"{CATALOG_BASE}/{plan_id}/pricing/deployment",
            timeout=ingest_settings.http_timeout_seconds,
        )
