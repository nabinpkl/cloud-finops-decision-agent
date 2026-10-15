"""Oracle Cloud (OCI) pricing ingest from the public price list API, stored raw."""

from __future__ import annotations

import httpx

from ingest.config import ingest_settings
from ingest.raw_snapshot import RawSnapshot, run_ingest

PROVIDER = "oracle"
SERVICE = "all-products"
PRICELIST_URL = "https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/"
PRODUCTS_FILE = "products.json"

# Oracle publishes one global list price per SKU. The endpoint returns the full
# catalog (~670 items, ~3 MB) in one response with no pagination and no auth.
# Compute - Virtual Machine, Compute - Bare Metal, and Compute - GPU SKUs live
# here alongside everything else. Modern shapes (E3-E6, X9, A1) price OCPU and
# memory separately, so a "4 vCPU 8 GB" answer combines two SKUs. Regional
# pricing variations are not exposed by this endpoint.


async def fetch(snapshot: RawSnapshot, client: httpx.AsyncClient) -> None:
    await snapshot.fetch(client, PRODUCTS_FILE, PRICELIST_URL, timeout=ingest_settings.http_timeout_seconds)


def main() -> None:
    run_ingest(
        provider=PROVIDER,
        service=SERVICE,
        source_url=PRICELIST_URL,
        fetch=fetch,
        hint="Check network connectivity and that the Oracle price list endpoint is reachable.",
        description="Fetch Oracle Cloud pricing snapshot.",
    )


if __name__ == "__main__":
    main()
