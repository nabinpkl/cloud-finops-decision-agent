"""IBM Global Catalog and deployment-pricing walk."""

from __future__ import annotations

import asyncio

import httpx

from gates._shared import fetch_polite, now_utc
from gates.ibm.constants import (
    CATALOG_BASE,
    CATALOG_FIRST_URL,
    COMPUTE_SERVICE_NAMES,
    PRICING_CONCURRENCY,
)
from gates.ibm.logging import log, on_retry_log


async def fetch_all_services(client: httpx.AsyncClient) -> tuple[list[dict], int]:
    services: list[dict] = []
    pages = 0
    next_url: str | None = CATALOG_FIRST_URL
    while next_url:
        pages += 1
        t0 = now_utc()
        resp = await fetch_polite(client, next_url, timeout=120.0, on_retry=on_retry_log)
        dt = (now_utc() - t0).total_seconds()
        page = resp.json()
        chunk = page.get("resources", [])
        services.extend(chunk)
        log(
            f"catalog page {pages}: 200 in {dt:.2f}s "
            f"resources+={len(chunk)} total={len(services)}"
        )
        next_url = page.get("next") or None
    return services, pages


async def fetch_children(client: httpx.AsyncClient, service_id: str) -> list[dict]:
    plans: list[dict] = []
    first_url = f"{CATALOG_BASE}/{service_id}/%2A"
    next_url: str | None = first_url
    while next_url:
        resp = await fetch_polite(client, next_url, timeout=120.0, on_retry=on_retry_log)
        page = resp.json()
        plans.extend(page.get("resources", []))
        next_url = page.get("next") or None
    return plans


async def fetch_plan_pricing(
    client: httpx.AsyncClient, plan_id: str, sem: asyncio.Semaphore
) -> dict:
    url = f"{CATALOG_BASE}/{plan_id}/pricing/deployment"
    async with sem:
        resp = await fetch_polite(client, url, timeout=120.0, on_retry=on_retry_log)
    return resp.json()


async def fetch_compute_pricing(
    client: httpx.AsyncClient, services_by_name: dict[str, dict]
) -> tuple[dict[str, dict], int, set[str]]:
    compute: dict[str, dict] = {}
    total_calls = 0
    regions: set[str] = set()
    sem = asyncio.Semaphore(PRICING_CONCURRENCY)

    for name in COMPUTE_SERVICE_NAMES:
        svc = services_by_name.get(name)
        if svc is None:
            log(f"compute service {name!r} not found in catalog; skipping")
            compute[name] = {"service_id": None, "plans": [], "pricing": {}}
            continue
        service_id = svc.get("id")
        if not service_id:
            log(f"compute service {name!r} has no id in catalog; skipping")
            compute[name] = {"service_id": None, "plans": [], "pricing": {}}
            continue
        plans = await fetch_children(client, service_id)
        log(f"{name}: {len(plans)} plans, fetching deployment pricing...")

        plan_ids = [pid for plan in plans if (pid := plan.get("id"))]
        results = await asyncio.gather(
            *(fetch_plan_pricing(client, pid, sem) for pid in plan_ids),
            return_exceptions=True,
        )

        pricing: dict[str, dict] = {}
        for pid, result in zip(plan_ids, results):
            if isinstance(result, BaseException):
                log(f"  plan {pid} pricing failed: {result!r}")
                pricing[pid] = {"error": str(result)}
                continue
            pricing[pid] = result
            total_calls += 1
            for dep in result.get("resources", []):
                region = dep.get("deployment_region")
                if region:
                    regions.add(region)

        compute[name] = {"service_id": service_id, "plans": plans, "pricing": pricing}
        log(f"{name}: priced {len(pricing)} plans")

    return compute, total_calls, regions


async def fetch_all() -> tuple[list[dict], int, dict[str, dict], int, set[str]]:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        services, page_count = await fetch_all_services(client)
        services_by_name = {name: service for service in services if (name := service.get("name"))}
        compute, pricing_calls, regions = await fetch_compute_pricing(client, services_by_name)
    return services, page_count, compute, pricing_calls, regions

