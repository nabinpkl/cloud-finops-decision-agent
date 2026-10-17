"""Scaleway Instances prices via the public Product Catalog API, stored raw page by page."""

from __future__ import annotations

import httpx

from ingest._shared import IngestError
from ingest.config import ingest_settings
from ingest.raw_snapshot import PageGuard, RawSnapshot, run_ingest

PROVIDER = "scaleway"
SERVICE = "public-catalog-instances"
PRODUCTS_URL = "https://api.scaleway.com/product-catalog/v2alpha1/public-catalog/products"
PAGE_SIZE = 100

# The public catalog needs no credentials. `product_types=instance` narrows it to
# Instances (about 1000 products in Oct 2026; the whole catalog is about 5600).
# Pages are numbered from 1 and each carries `total_count`; a page lists one
# product per offer and zone (`sku` like /instance/server/pop2_4c_16g/fr-par-1)
# with `price.retail_price` as {currency_code: EUR, units, nanos} per
# `unit_of_measure` (hour, or minute for a few GPU offers), `status`
# (general_availability or retired) and `properties.hardware` (cpu, ram, gpu).
# Prices are in EUR only and stored as published (ADR 0019).


def page_name(n: int) -> str:
    return f"products-{n:04d}.json"


async def fetch(snapshot: RawSnapshot, client: httpx.AsyncClient) -> None:
    guard = PageGuard("scaleway products")
    seen = 0
    n = 1
    while True:
        name = page_name(n)
        params = {"product_types": "instance", "page_size": PAGE_SIZE, "page": n}
        await snapshot.fetch(client, name, PRODUCTS_URL, params=params, timeout=ingest_settings.http_timeout_seconds)
        doc = snapshot.read_json(name)
        products = doc.get("products") if isinstance(doc, dict) else None
        total = doc.get("total_count") if isinstance(doc, dict) else None
        if not isinstance(products, list) or not isinstance(total, int):
            raise IngestError(f"{name}: no products[] and total_count; Scaleway's catalog shape changed")
        seen += len(products)
        if seen >= total:
            return
        if not products:
            raise IngestError(f"{name}: empty page after {seen} of {total} products")
        n += 1
        guard.follow(str(n))


def main() -> None:
    run_ingest(
        provider=PROVIDER,
        service=SERVICE,
        source_url=PRODUCTS_URL,
        fetch=fetch,
        allowed_hosts=frozenset({httpx.URL(PRODUCTS_URL).host}),
        hint="Check network connectivity and that api.scaleway.com is reachable.",
        description="Fetch Scaleway Instances pricing snapshot.",
    )


if __name__ == "__main__":
    main()
