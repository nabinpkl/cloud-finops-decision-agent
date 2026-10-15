"""Vultr plans ingest via the public /v2/plans API, stored raw page by page."""

from __future__ import annotations

import httpx

from ingest._shared import IngestError
from ingest.config import ingest_settings
from ingest.raw_snapshot import PageGuard, RawSnapshot, run_ingest

PROVIDER = "vultr"
SERVICE = "plans"
PLANS_URL = "https://api.vultr.com/v2/plans"
PER_PAGE = 500  # API maximum; the default of 100 cut the catalog (138 plans in Oct 2026).

# /v2/plans is no-auth and cursor-paged: meta.links.next carries the cursor for the
# next page and is empty on the last. Each plan carries vcpu_count, ram, disk,
# bandwidth, monthly_cost, hourly_cost, type (vc2/vhf/vhp/etc.), and a locations[]
# list of region codes where the plan is available. Price is global per plan;
# locations[] is availability, not a pricing dimension.


def page_name(n: int) -> str:
    return f"plans-{n:04d}.json"


async def fetch(snapshot: RawSnapshot, client: httpx.AsyncClient) -> None:
    params: dict[str, str | int] = {"per_page": PER_PAGE}
    guard = PageGuard("vultr plans")
    n = 1
    while True:
        name = page_name(n)
        await snapshot.fetch(client, name, PLANS_URL, params=params, timeout=ingest_settings.http_timeout_seconds)
        links = (snapshot.read_json(name).get("meta") or {}).get("links")
        if not isinstance(links, dict) or "next" not in links:
            raise IngestError(f"{name}: no meta.links.next; Vultr's paging shape changed")
        cursor = links["next"]
        if not cursor:
            return
        guard.follow(cursor)
        params = {"per_page": PER_PAGE, "cursor": cursor}
        n += 1


def main() -> None:
    run_ingest(
        provider=PROVIDER,
        service=SERVICE,
        source_url=PLANS_URL,
        fetch=fetch,
        allowed_hosts=frozenset({httpx.URL(PLANS_URL).host}),
        hint="Check network connectivity and that api.vultr.com is reachable.",
        description="Fetch Vultr pricing snapshot.",
    )


if __name__ == "__main__":
    main()
