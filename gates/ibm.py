"""IBM Cloud pricing gate: catalog + per-plan deployment pricing for compute services."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from datetime import timedelta

import httpx

from gates._shared import (
    FileRecord,
    PROJECT_ROOT,
    emit,
    fetch_polite,
    is_fresh,
    iso_compact,
    iso_z,
    latest_receipt_path,
    now_utc,
    sha256_bytes,
    store_root,
)

PROVIDER = "ibm"
SERVICE = "global-catalog+compute-pricing"
CATALOG_BASE = "https://globalcatalog.cloud.ibm.com/api/v1"
CATALOG_FIRST_URL = f"{CATALOG_BASE}?q=kind:service"
FRESHNESS = timedelta(hours=24)

# IBM's Global Catalog is hierarchical and three-hop:
#   1. Catalog query returns ~319 services across the whole IBM Cloud product line.
#   2. Filter to compute service names (below). Each entry has a children_url that,
#      paginated 50/page, lists every plan (e.g. bx3d-2x10, mx3d-8x80).
#   3. Each plan has /pricing/deployment which returns per-region pricing
#      (deployment_region + metrics + amounts by country/currency).
# The plain /pricing endpoint at the service level returns empty metrics; at the
# plan level it returns OS surcharges with $0 for base instance-hours because the
# real base price is fully regional. /pricing/deployment is the only place real
# numbers live for compute, and a single call returns every region the plan
# exists in.
#
# v0 compute scope: VPC virtual servers, bare metal, dedicated hosts. Power
# Systems (power-iaas) and reservations (is.reservation) are out of v0.
COMPUTE_SERVICE_NAMES: list[str] = [
    "is.instance",
    "is.bare-metal-server",
    "is.dedicated-host",
]

# Bound concurrency so a ~250-call plan-pricing walk completes in seconds without
# hammering globalcatalog.cloud.ibm.com. No documented limit on this endpoint;
# the polite helper handles 429 if one materializes.
PRICING_CONCURRENCY = 8

STORE_ROOT = store_root(PROVIDER)


@dataclass
class Receipt:
    success: bool
    provider: str
    service: str
    source_url: str
    store_dir: str
    fetched_at: str
    regions_included: list[str]
    compute_services_included: list[str]
    service_count: int
    catalog_page_count: int
    plan_count: int
    pricing_call_count: int
    files: list[FileRecord]
    total_size_bytes: int


def log(msg: str) -> None:
    print(f"[ibm] {msg}", file=sys.stderr, flush=True)


def on_retry_log(attempt: int, status: int, wait: float) -> None:
    log(f"retry attempt {attempt}: {status}, waiting {wait:.1f}s")


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
    # children_url ends in "/*"; pagination uses ?_offset=N. URL-encode the wildcard.
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
    """For each compute service, list plans and fetch deployment pricing per plan.

    Returns (compute_map, total_pricing_calls, regions_seen).
    compute_map shape: {service_name: {"service_id": str, "plans": [<plan>], "pricing": {plan_id: <deployment_pricing>}}}
    """
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

        plan_ids = [pid for p in plans if (pid := p.get("id"))]
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
        services_by_name = {nm: s for s in services if (nm := s.get("name"))}
        compute, pricing_calls, regions = await fetch_compute_pricing(client, services_by_name)
    return services, page_count, compute, pricing_calls, regions


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch IBM Cloud catalog and compute pricing.")
    parser.add_argument("--force", action="store_true", help="Bypass the 24h freshness rule.")
    args = parser.parse_args()

    if not args.force:
        latest = latest_receipt_path(PROVIDER)
        if latest is not None and is_fresh(latest, FRESHNESS):
            emit(json.loads(latest.read_text()))

    fetched_at = now_utc()
    snapshot_dir = STORE_ROOT / iso_compact(fetched_at)

    try:
        services, page_count, compute, pricing_calls, regions = asyncio.run(fetch_all())
    except httpx.HTTPError as exc:
        emit(
            {
                "success": False,
                "provider": PROVIDER,
                "error": str(exc),
                "hint": "Check network connectivity and that globalcatalog.cloud.ibm.com is reachable.",
            },
            code=1,
        )

    services_payload = {
        "fetched_at": iso_z(fetched_at),
        "source_url": CATALOG_FIRST_URL,
        "page_count": page_count,
        "service_count": len(services),
        "services": services,
    }
    services_bytes = json.dumps(services_payload, indent=2).encode("utf-8")

    compute_payload = {
        "fetched_at": iso_z(fetched_at),
        "compute_services_included": COMPUTE_SERVICE_NAMES,
        "compute": compute,
    }
    compute_bytes = json.dumps(compute_payload, indent=2).encode("utf-8")

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "services.json").write_bytes(services_bytes)
    (snapshot_dir / "compute.json").write_bytes(compute_bytes)

    files = [
        FileRecord(
            name="services.json",
            hash=sha256_bytes(services_bytes),
            size_bytes=len(services_bytes),
            source_url=CATALOG_FIRST_URL,
        ),
        FileRecord(
            name="compute.json",
            hash=sha256_bytes(compute_bytes),
            size_bytes=len(compute_bytes),
            source_url=f"{CATALOG_BASE}/<plan-id>/pricing/deployment",
        ),
    ]

    plan_count = sum(len(c.get("plans", [])) for c in compute.values())

    receipt = Receipt(
        success=True,
        provider=PROVIDER,
        service=SERVICE,
        source_url=CATALOG_FIRST_URL,
        store_dir=str(snapshot_dir.relative_to(PROJECT_ROOT)),
        fetched_at=iso_z(fetched_at),
        regions_included=sorted(regions),
        compute_services_included=COMPUTE_SERVICE_NAMES,
        service_count=len(services),
        catalog_page_count=page_count,
        plan_count=plan_count,
        pricing_call_count=pricing_calls,
        files=files,
        total_size_bytes=sum(f.size_bytes for f in files),
    )

    (snapshot_dir / "receipt.json").write_text(json.dumps(asdict(receipt), indent=2))
    emit(asdict(receipt))


if __name__ == "__main__":
    main()
