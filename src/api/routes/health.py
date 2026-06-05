"""Health and data-quality routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from normalize.data_quality import compute_envelope
from normalize.index import SUPPORTED_PROVIDERS

router = APIRouter()


@router.get("/health")
def health() -> dict[str, Any]:
    # Liveness is the process answering at all. Freshness and dependency status
    # come from the data_quality envelope: each provider's snapshot age plus an
    # overall rollup that goes "broken" when a provider has no usable snapshot.
    envelope = compute_envelope(SUPPORTED_PROVIDERS)
    return {
        "status": "ok",
        "providers": SUPPORTED_PROVIDERS,
        "data_quality": envelope,
    }
