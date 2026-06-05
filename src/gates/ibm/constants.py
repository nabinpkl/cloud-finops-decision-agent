"""IBM gate constants."""

from __future__ import annotations

from datetime import timedelta

from gates._shared import store_root

PROVIDER = "ibm"
SERVICE = "global-catalog+compute-pricing"
CATALOG_BASE = "https://globalcatalog.cloud.ibm.com/api/v1"
CATALOG_FIRST_URL = f"{CATALOG_BASE}?q=kind:service"
FRESHNESS = timedelta(hours=24)

COMPUTE_SERVICE_NAMES: list[str] = [
    "is.instance",
    "is.bare-metal-server",
    "is.dedicated-host",
]

PRICING_CONCURRENCY = 8
STORE_ROOT = store_root(PROVIDER)

