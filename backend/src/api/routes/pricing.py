"""Deterministic pricing query routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from normalize.index import SUPPORTED_PROVIDERS
from normalize.query.service import compare, lookup
from normalize.wire import wire_response

router = APIRouter()

SUPPORTED_PROVIDER_SET = set(SUPPORTED_PROVIDERS)


class CompareRequest(BaseModel):
    vcpu: int = Field(gt=0)
    ram_gb: float = Field(gt=0)
    region: str
    family: str = "any"
    providers: list[str] | None = Field(default=None, max_length=len(SUPPORTED_PROVIDERS))
    expand: str = "cheapest"

    @field_validator("providers")
    @classmethod
    def _providers_supported(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        unknown = sorted(set(value) - SUPPORTED_PROVIDER_SET)
        if unknown:
            raise ValueError(f"unsupported providers: {', '.join(unknown)}")
        return value


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
    if provider not in SUPPORTED_PROVIDER_SET:
        raise HTTPException(status_code=422, detail=f"unsupported provider: {provider}")
    result = lookup(provider=provider, instance_type=instance_type, region=region)
    return wire_response(result)
