"""Typed response models for normalize.query.

These are the contract-bearing shapes returned by compare() and lookup().
The public functions still return dictionaries for the CLI, FastAPI, and
agent-tool surfaces, but serialization now goes through Pydantic models instead
of hand-written dataclass to_dict methods.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class QueryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CitationBlock(QueryModel):
    source_url: str
    store_path: str
    json_path: str
    fetched_at: str
    age_hours: float


class CompositeCitationEntry(QueryModel):
    kind: Literal["rate"]
    rate_unit: Literal["per_vcpu_hour", "per_ocpu_hour", "per_gb_ram_hour"]
    rate: float
    quantity: float
    contribution_usd: float
    source_url: str
    store_path: str
    json_path: str
    fetched_at: str
    age_hours: float


class CompositeCitation(QueryModel):
    composite: list[CompositeCitationEntry]
    synthesis: dict[Literal["rule", "formula"], str]


class CandidateBrief(QueryModel):
    instance_type: str
    vcpu: int | None
    ram_gb: float | None
    region_native: str
    hourly_usd: float | None
    monthly_usd: float | None


class CompareResult(QueryModel):
    provider: str
    instance_type: str
    region_native: str
    vcpu_actual: int
    ram_gb_actual: float
    hourly_usd: float | None
    monthly_usd: float | None
    considered_count: int
    citation: CitationBlock | CompositeCitation
    considered: list[CandidateBrief] = Field(default_factory=list)
    synthesized: Literal[True] | None = None

    def to_public_dict(self) -> dict[str, Any]:
        out = self.model_dump(mode="json", exclude_none=True)
        if not self.considered:
            out.pop("considered", None)
        return out


class CompareRequest(QueryModel):
    vcpu: int
    ram_gb: float
    region: str
    family: str
    providers: list[str]


class UnmetRequirement(QueryModel):
    provider: str
    reason: str


class CompareResponse(QueryModel):
    request: CompareRequest
    results: list[CompareResult]
    ranked_by: Literal["monthly_usd"] = "monthly_usd"
    unmet_requirements: list[UnmetRequirement]
    data_quality: dict[str, Any]

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "request": self.request.model_dump(mode="json"),
            "results": [result.to_public_dict() for result in self.results],
            "ranked_by": self.ranked_by,
            "unmet_requirements": [
                item.model_dump(mode="json") for item in self.unmet_requirements
            ],
            "data_quality": self.data_quality,
        }


class LookupRequest(QueryModel):
    provider: str
    instance_type: str
    region: str


class LookupResult(QueryModel):
    provider: str
    instance_type: str
    family: str
    region_native: str
    vcpu: int
    ram_gb: float
    hourly_usd: float | None
    monthly_usd: float | None
    citation: CitationBlock


class LookupResponse(QueryModel):
    request: LookupRequest
    result: LookupResult | None
    data_quality: dict[str, Any]
    unmet_requirements: list[UnmetRequirement]

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "request": self.request.model_dump(mode="json"),
            "result": (
                self.result.model_dump(mode="json", exclude_none=True)
                if self.result is not None
                else None
            ),
            "data_quality": self.data_quality,
            "unmet_requirements": [
                item.model_dump(mode="json") for item in self.unmet_requirements
            ],
        }
