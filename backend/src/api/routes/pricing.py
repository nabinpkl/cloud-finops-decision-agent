"""Deterministic pricing query routes."""

from __future__ import annotations

from typing import Annotated
from typing import Any

from fastapi import APIRouter, Query

from normalize.query.inputs import CompareQueryArgs, LookupQueryArgs
from normalize.query.service import compare, lookup
from normalize.wire import wire_response

router = APIRouter()


@router.post("/compare")
def post_compare(req: CompareQueryArgs) -> dict[str, Any]:
    result = compare(
        vcpu=req.vcpu,
        ram_gb=req.ram_gb,
        region=req.region,
        family=req.family,
        providers=list(req.providers) if req.providers is not None else None,
        expand=req.expand,
    )
    return wire_response(result)


@router.get("/lookup")
def get_lookup(req: Annotated[LookupQueryArgs, Query()]) -> dict[str, Any]:
    result = lookup(
        provider=req.provider,
        instance_type=req.instance_type,
        region=req.region,
    )
    return wire_response(result)
