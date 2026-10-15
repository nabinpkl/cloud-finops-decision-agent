"""Azure Virtual Machines pricing ingest via the Retail Prices API, stored raw page by page."""

from __future__ import annotations

from urllib.parse import quote

import httpx

from ingest.config import ingest_settings
from ingest.raw_snapshot import RawSnapshot, run_ingest

PROVIDER = "azure"
SERVICE = "virtual-machines"
RETAIL_PRICES_BASE = "https://prices.azure.com/api/retail/prices"
REGIONS: list[str] = ["eastus", "westeurope", "southeastasia"]

# An unfiltered "all Virtual Machines" fetch runs to 500+ pages of 1000 items and
# trips an unpublished rate limit (~470 sequential requests => 429 with
# Retry-After=60). So each region is fetched on its own, `priceType eq
# 'Consumption'` only: that keeps pay-as-you-go and spot/low-priority meters and
# drops reservations and savings plans. Page size is fixed at 1000 server-side
# ($top is ignored); NextPageLink is the full URL of the next page and is null on
# the last.


def filter_for(region: str) -> str:
    return (
        f"serviceName eq 'Virtual Machines' "
        f"and priceType eq 'Consumption' "
        f"and armRegionName eq '{region}'"
    )


def first_url_for(region: str) -> str:
    return f"{RETAIL_PRICES_BASE}?$filter={quote(filter_for(region))}"


def page_name(region: str, n: int) -> str:
    return f"{region}-{n:04d}.json"


async def fetch(snapshot: RawSnapshot, client: httpx.AsyncClient) -> None:
    for region in REGIONS:
        url: str | None = first_url_for(region)
        n = 1
        while url:
            name = page_name(region, n)
            await snapshot.fetch(client, name, url, timeout=ingest_settings.http_timeout_seconds)
            url = snapshot.read_json(name).get("NextPageLink") or None
            n += 1


def main() -> None:
    run_ingest(
        provider=PROVIDER,
        service=SERVICE,
        source_url=RETAIL_PRICES_BASE,
        fetch=fetch,
        hint="Check network connectivity and that the Retail Prices API endpoint is reachable.",
        description="Fetch Azure Virtual Machines pricing snapshot.",
    )


if __name__ == "__main__":
    main()
