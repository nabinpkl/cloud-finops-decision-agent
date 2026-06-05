"""IBM ingest constants."""

from __future__ import annotations

from ingest._shared import store_root
from ingest.config import ingest_settings

PROVIDER = "ibm"
SERVICE = "global-catalog+compute-pricing"
CATALOG_BASE = "https://globalcatalog.cloud.ibm.com/api/v1"
CATALOG_FIRST_URL = f"{CATALOG_BASE}?q=kind:service"
FRESHNESS = ingest_settings.snapshot_freshness

COMPUTE_SERVICE_NAMES: list[str] = [
    "is.instance",
    "is.bare-metal-server",
    "is.dedicated-host",
]

PRICING_CONCURRENCY = ingest_settings.ibm_pricing_concurrency
STORE_ROOT = store_root(PROVIDER)
