"""RunPod GPU prices via its public GraphQL API, stored raw."""

from __future__ import annotations

import httpx

from ingest.config import ingest_settings
from ingest.raw_snapshot import RawSnapshot, run_ingest

PROVIDER = "runpod"
SERVICE = "gpu-types"
GRAPHQL_URL = "https://api.runpod.io/graphql"

# RunPod answers this query without an API key. It prices GPUs, not machines:
# per GPU-hour on Secure Cloud (RunPod's data centers) and Community Cloud
# (vetted third-party hosts), with `secureCloud`/`communityCloud` saying whether
# the tier is offered at all. When a tier is not offered its price field holds a
# placeholder (0 or 0.5), so the builder reads a price only for an offered tier.
# Spot prices come back null without an API key. The query is recorded in the
# receipt (`request_body`) so the file can be fetched again exactly.
QUERY = (
    "{ gpuTypes { id displayName memoryInGb secureCloud communityCloud "
    "securePrice communityPrice secureSpotPrice communitySpotPrice } }"
)
FILE = "gpu-types.json"


async def fetch(snapshot: RawSnapshot, client: httpx.AsyncClient) -> None:
    await snapshot.fetch(
        client,
        FILE,
        GRAPHQL_URL,
        json_body={"query": QUERY},
        timeout=ingest_settings.http_short_timeout_seconds,
    )
    doc = snapshot.read_json(FILE)
    errors = doc.get("errors") if isinstance(doc, dict) else None
    data = doc.get("data") if isinstance(doc, dict) else None
    if errors or not isinstance(data, dict) or not isinstance(data.get("gpuTypes"), list):
        snapshot.refuse(FILE, f"GraphQL answered without data.gpuTypes ({str(errors)[:200]})")


def main() -> None:
    run_ingest(
        provider=PROVIDER,
        service=SERVICE,
        source_url=GRAPHQL_URL,
        fetch=fetch,
        allowed_hosts=frozenset({httpx.URL(GRAPHQL_URL).host}),
        hint="Check network connectivity and that api.runpod.io is reachable.",
        description="Fetch RunPod GPU pricing snapshot.",
    )


if __name__ == "__main__":
    main()
