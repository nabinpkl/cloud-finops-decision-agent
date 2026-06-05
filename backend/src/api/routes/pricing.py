"""Deterministic pricing query routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from normalize.wire import wire_response
from normalize.query import compare, lookup

router = APIRouter()


class CompareRequest(BaseModel):
    vcpu: int = Field(gt=0)
    ram_gb: float = Field(gt=0)
    region: str
    family: str = "any"
    providers: list[str] | None = None
    expand: str = "cheapest"


@router.post("/compare")
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


@router.get("/lookup")
def get_lookup(
    provider: str = Query(...),
    instance_type: str = Query(...),
    region: str = Query(...),
) -> dict[str, Any]:
    result = lookup(provider=provider, instance_type=instance_type, region=region)
    return wire_response(result)
