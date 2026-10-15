"""Linode types ingest via the public /v4/linode/types API, stored raw page by page."""

from __future__ import annotations

import httpx

from ingest._shared import IngestError
from ingest.config import ingest_settings
from ingest.raw_snapshot import PageGuard, RawSnapshot, run_ingest

PROVIDER = "linode"
SERVICE = "types"
TYPES_URL = "https://api.linode.com/v4/linode/types"
PAGE_SIZE = 500  # API maximum.

# /v4/linode/types is no-auth and page-numbered (`page`, `pages`); ~75 types fit on
# one page today. Each type carries a base price.{hourly,monthly} that applies
# globally, plus a region_prices[] list of per-region overrides (id, hourly,
# monthly) for regions that price differently from the base.


def page_name(n: int) -> str:
    return f"types-{n:04d}.json"


async def fetch(snapshot: RawSnapshot, client: httpx.AsyncClient) -> None:
    guard = PageGuard("linode types")
    n = 1
    while True:
        name = page_name(n)
        await snapshot.fetch(
            client,
            name,
            TYPES_URL,
            params={"page": n, "page_size": PAGE_SIZE},
            timeout=ingest_settings.http_timeout_seconds,
        )
        pages = snapshot.read_json(name).get("pages")
        if not isinstance(pages, int):
            raise IngestError(f"{name}: no integer `pages` field; Linode's paging shape changed")
        if n >= pages:
            return
        n += 1
        guard.follow(str(n))


def main() -> None:
    run_ingest(
        provider=PROVIDER,
        service=SERVICE,
        source_url=TYPES_URL,
        fetch=fetch,
        allowed_hosts=frozenset({httpx.URL(TYPES_URL).host}),
        hint="Check network connectivity and that api.linode.com is reachable.",
        description="Fetch Linode pricing snapshot.",
    )


if __name__ == "__main__":
    main()
