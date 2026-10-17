"""Exoscale compute prices and instance types, both public, stored raw."""

from __future__ import annotations

import httpx

from ingest._shared import IngestError
from ingest.config import ingest_settings
from ingest.raw_snapshot import RawSnapshot, run_ingest

PROVIDER = "exoscale"
SERVICE = "compute"

# Two public responses. The portal's pricing document is what exoscale.com's own
# price pages read; it is not part of the documented API, so a shape change fails
# the fetch loudly. It maps `running_<size>` (standard family) and
# `running_<family>_<size>` to an hourly price per currency (`chf`, `eur`, `usd`),
# one price for every zone. The v2 API's instance-type list (no credentials
# needed) gives each type's family, size, cpus, memory in bytes, gpus and the
# zones it runs in; any zone's endpoint lists all zones.
PRICING_URL = "https://portal.exoscale.com/api/pricing/opencompute"
INSTANCE_TYPES_URL = "https://api-ch-gva-2.exoscale.com/v2/instance-type"


async def fetch(snapshot: RawSnapshot, client: httpx.AsyncClient) -> None:
    timeout = ingest_settings.http_short_timeout_seconds
    await snapshot.fetch(client, "pricing-opencompute.json", PRICING_URL, timeout=timeout)
    pricing = snapshot.read_json("pricing-opencompute.json")
    if not isinstance(pricing, dict) or not isinstance(pricing.get("usd"), dict):
        raise IngestError("pricing-opencompute.json: no usd prices; Exoscale's pricing shape changed")
    await snapshot.fetch(client, "instance-types.json", INSTANCE_TYPES_URL, timeout=timeout)
    types = snapshot.read_json("instance-types.json")
    if not isinstance(types, dict) or not isinstance(types.get("instance-types"), list):
        raise IngestError("instance-types.json: no instance-types[]; Exoscale's API shape changed")


def main() -> None:
    run_ingest(
        provider=PROVIDER,
        service=SERVICE,
        source_url=PRICING_URL,
        fetch=fetch,
        allowed_hosts=frozenset({httpx.URL(PRICING_URL).host, httpx.URL(INSTANCE_TYPES_URL).host}),
        hint="Check network connectivity and that portal.exoscale.com and api-ch-gva-2.exoscale.com are reachable.",
        description="Fetch Exoscale compute pricing snapshot.",
    )


if __name__ == "__main__":
    main()
