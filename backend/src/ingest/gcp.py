"""GCP Compute Engine pricing ingest via the Cloud Billing Catalog API, stored raw page by page."""

from __future__ import annotations

import os

import httpx

from ingest._shared import emit, load_dotenv_if_present
from ingest.config import ingest_settings
from ingest.raw_snapshot import RawSnapshot, run_ingest

PROVIDER = "gcp"
SERVICE = "compute"
COMPUTE_SERVICE_ID = "6F81-5844-456A"
CATALOG_BASE = f"https://cloudbilling.googleapis.com/v1/services/{COMPUTE_SERVICE_ID}/skus"
PAGE_SIZE = 5000  # API hard maximum; values above this return HTTP 400.

# The API key travels in the X-goog-api-key header, never in the URL, so it never
# reaches a receipt. nextPageToken is empty on the last page.


def page_name(n: int) -> str:
    return f"skus-{n:04d}.json"


async def fetch(snapshot: RawSnapshot, client: httpx.AsyncClient) -> None:
    params: dict[str, str | int] = {"pageSize": PAGE_SIZE}
    n = 1
    while True:
        name = page_name(n)
        await snapshot.fetch(client, name, CATALOG_BASE, params=params, timeout=ingest_settings.http_timeout_seconds)
        token = snapshot.read_json(name).get("nextPageToken")
        if not token:
            return
        params = {"pageSize": PAGE_SIZE, "pageToken": token}
        n += 1


def _api_key() -> str:
    load_dotenv_if_present()
    api_key = os.environ.get("GCP_API_KEY")
    if not api_key:
        emit(
            {
                "success": False,
                "provider": PROVIDER,
                "error": "GCP_API_KEY not set",
                "hint": "Provision a key with the Cloud Billing API enabled. Add GCP_API_KEY=... to .env at the project root. See .env.example.",
            },
            code=1,
        )
    return api_key


def main() -> None:
    load_dotenv_if_present()
    run_ingest(
        provider=PROVIDER,
        service=SERVICE,
        source_url=CATALOG_BASE,
        fetch=fetch,
        hint="Check GCP_API_KEY validity and Cloud Billing API quota.",
        description="Fetch GCP Compute Engine pricing snapshot.",
        client_headers={"X-goog-api-key": os.environ.get("GCP_API_KEY", "")},
        check_ready=_api_key,
    )


if __name__ == "__main__":
    main()
