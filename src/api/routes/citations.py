"""Citation excerpt routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ingest.config import ingest_settings
from normalize.citations.excerpt import build_excerpt
from normalize.index import SUPPORTED_PROVIDERS

STORE_ROOT = ingest_settings.store_root_path

router = APIRouter()


@router.get("/citation/excerpt")
def get_excerpt(
    provider: str = Query(...),
    snapshot_iso: str = Query(...),
    filename: str = Query(...),
    path: str = Query(..., description="json_path into the snapshot file"),
    context: int = Query(4, ge=0, le=40),
) -> dict[str, Any]:
    abs_path = _resolve_snapshot_file(provider, snapshot_iso, filename)
    return build_excerpt(abs_path=abs_path, json_path=path, context=context)


def _resolve_snapshot_file(provider: str, snapshot_iso: str, filename: str) -> Path:
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=404, detail=f"unknown provider {provider!r}")
    for label, value in (("snapshot_iso", snapshot_iso), ("filename", filename)):
        if "/" in value or "\\" in value or ".." in value or value in ("", "."):
            raise HTTPException(status_code=400, detail=f"invalid {label}")

    candidate = (STORE_ROOT / provider / snapshot_iso / filename).resolve()
    provider_root = (STORE_ROOT / provider).resolve()
    if not candidate.is_relative_to(provider_root):
        raise HTTPException(status_code=400, detail="path escapes provider store")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="snapshot file not found")
    return candidate
