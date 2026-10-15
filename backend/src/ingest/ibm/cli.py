"""IBM ingest CLI."""

from __future__ import annotations

from ingest.ibm.catalog import fetch
from ingest.ibm.constants import CATALOG_FIRST_URL, CATALOG_HOST, PROVIDER, SERVICE
from ingest.raw_snapshot import run_ingest


def main() -> None:
    run_ingest(
        provider=PROVIDER,
        service=SERVICE,
        source_url=CATALOG_FIRST_URL,
        fetch=fetch,
        allowed_hosts=frozenset({CATALOG_HOST}),
        hint="Check network connectivity and that globalcatalog.cloud.ibm.com is reachable.",
        description="Fetch IBM Cloud catalog and compute pricing.",
    )
