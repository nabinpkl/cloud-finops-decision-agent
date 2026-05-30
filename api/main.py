"""FastAPI wrapper over the normalize query layer, per ADR 0008.

Surface:
  POST /compare            -> normalize.query.compare
  GET  /lookup             -> normalize.query.lookup
  GET  /citation/excerpt   -> normalize.citation_excerpt.build_excerpt
  GET  /health

The wire shape matches the query layer's dicts with one change: the internal
`store_path` filesystem path is dropped from every citation and replaced by a
logical `snapshot` ref ({provider, snapshot_iso, filename}). The excerpt
endpoint consumes that ref. `normalize/` itself is unchanged; the translation
lives here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from api.config import settings
from api.observability import init_observability
from api.transport import router as transport_router
from api.wire import wire_response
from gates._shared import PROJECT_ROOT
from normalize.citation_excerpt import build_excerpt
from normalize.data_quality import compute_envelope
from normalize.index import SUPPORTED_PROVIDERS
from normalize.query import compare, lookup

STORE_ROOT = PROJECT_ROOT / "store"

app = FastAPI(title="cloud-finops-decision-agent", version="0.0.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
init_observability(app)
app.include_router(transport_router)


class CompareRequest(BaseModel):
    vcpu: int = Field(gt=0)
    ram_gb: float = Field(gt=0)
    region: str
    family: str = "any"
    providers: list[str] | None = None
    expand: str = "cheapest"


@app.get("/health")
def health() -> dict[str, Any]:
    # Liveness is the process answering at all. Freshness and dependency status
    # come from the data_quality envelope: each provider's snapshot age plus an
    # overall rollup that goes "broken" when a provider has no usable snapshot.
    envelope = compute_envelope(SUPPORTED_PROVIDERS)
    return {
        "status":        "ok",
        "providers":     SUPPORTED_PROVIDERS,
        "data_quality":  envelope,
    }


@app.post("/compare")
def post_compare(req: CompareRequest) -> dict[str, Any]:
    result = compare(
        vcpu=req.vcpu,
        ram_gb=req.ram_gb,
        region=req.region,
        family=req.family,
        providers=req.providers,
        expand=req.expand,
    )
    return wire_response(result)


@app.get("/lookup")
def get_lookup(
    provider: str = Query(...),
    instance_type: str = Query(...),
    region: str = Query(...),
) -> dict[str, Any]:
    result = lookup(provider=provider, instance_type=instance_type, region=region)
    return wire_response(result)


@app.get("/citation/excerpt")
def get_excerpt(
    provider: str = Query(...),
    snapshot_iso: str = Query(...),
    filename: str = Query(...),
    path: str = Query(..., description="json_path into the snapshot file"),
    context: int = Query(4, ge=0, le=40),
) -> dict[str, Any]:
    abs_path = _resolve_snapshot_file(provider, snapshot_iso, filename)
    return build_excerpt(abs_path=abs_path, json_path=path, context=context)


# ---------- excerpt path safety ----------


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
