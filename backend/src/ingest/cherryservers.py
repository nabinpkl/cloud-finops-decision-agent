"""Cherry Servers plans via the public /v1/plans endpoint, stored raw."""

from __future__ import annotations

import httpx

from ingest._shared import IngestError
from ingest.config import ingest_settings
from ingest.raw_snapshot import RawSnapshot, run_ingest

PROVIDER = "cherryservers"
SERVICE = "plans"
PLANS_URL = "https://api.cherryservers.com/v1/plans"

# /v1/plans needs no credentials and returns one JSON array of every plan
# (bare metal, VPS and VDS; 82 in Oct 2026), not paged. `currency=USD` asks for
# USD prices; the default is EUR. Each plan carries `slug`, `type`, `specs`
# (cpus.count/cores/threads, memory.total in GB), `pricing[]` with one entry per
# billing `unit` (Hourly, Monthly, Spot hourly for some bare metal, and longer
# terms) and `available_regions[]` (availability, with stock and spot counts).


async def fetch(snapshot: RawSnapshot, client: httpx.AsyncClient) -> None:
    await snapshot.fetch(
        client,
        "plans.json",
        PLANS_URL,
        params={"currency": "USD"},
        timeout=ingest_settings.http_timeout_seconds,
    )
    if not isinstance(snapshot.read_json("plans.json"), list):
        raise IngestError("plans.json: not a list of plans; Cherry Servers' API shape changed")


def main() -> None:
    run_ingest(
        provider=PROVIDER,
        service=SERVICE,
        source_url=PLANS_URL,
        fetch=fetch,
        allowed_hosts=frozenset({httpx.URL(PLANS_URL).host}),
        hint="Check network connectivity and that api.cherryservers.com is reachable.",
        description="Fetch Cherry Servers pricing snapshot.",
    )


if __name__ == "__main__":
    main()
