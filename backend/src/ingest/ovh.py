"""OVHcloud Public Cloud prices via the public order catalog, one file per subsidiary."""

from __future__ import annotations

import httpx

from ingest._shared import IngestError
from ingest.config import ingest_settings
from ingest.raw_snapshot import RawSnapshot, run_ingest

PROVIDER = "ovh"
SERVICE = "public-cloud-catalog"

# OVHcloud sells through subsidiaries, each with its own catalog, currency and
# regions. The US subsidiary prices its US regions (US-EAST-VA-1, US-WEST-OR-1) in
# USD; the FR subsidiary prices Europe, Canada and Asia-Pacific (DE1, GRA, SBG,
# WAW, UK1, BHS, SGP1, SYD1) in EUR, excluding VAT. Neither endpoint needs
# credentials. Each response is one JSON document (2 MB US, 8 MB FR) whose
# `addons[]` hold every flavor: `planCode` (b3-8.consumption is pay-as-you-go
# hourly), `pricings[].price` as an integer in 1e-8 of the currency,
# `configurations[name=region].values` (availability) and `blobs.technical`
# (cpu, memory, gpu, os).
CATALOGS = {
    "catalog-us.json": ("https://api.us.ovhcloud.com/1.0/order/catalog/public/cloud", "US"),
    "catalog-fr.json": ("https://api.ovh.com/1.0/order/catalog/public/cloud", "FR"),
}
SOURCE_URL = "https://www.ovhcloud.com/en/public-cloud/prices/"


async def fetch(snapshot: RawSnapshot, client: httpx.AsyncClient) -> None:
    for name, (url, subsidiary) in CATALOGS.items():
        await snapshot.fetch(
            client,
            name,
            url,
            params={"ovhSubsidiary": subsidiary},
            timeout=ingest_settings.http_timeout_seconds,
        )
        doc = snapshot.read_json(name)
        if not isinstance(doc, dict) or not isinstance(doc.get("addons"), list) or "locale" not in doc:
            raise IngestError(f"{name}: no addons[] and locale; OVHcloud's catalog shape changed")


def main() -> None:
    run_ingest(
        provider=PROVIDER,
        service=SERVICE,
        source_url=SOURCE_URL,
        fetch=fetch,
        allowed_hosts=frozenset(httpx.URL(url).host for url, _ in CATALOGS.values()),
        hint="Check network connectivity and that api.us.ovhcloud.com and api.ovh.com are reachable.",
        description="Fetch OVHcloud Public Cloud pricing snapshot.",
    )


if __name__ == "__main__":
    main()
