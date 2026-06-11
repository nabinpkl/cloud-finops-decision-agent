"""Pydantic models for model-emitted pricing answer plans."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AnswerPlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SnapshotRef(AnswerPlanModel):
    provider: str
    snapshot_iso: str
    filename: str


class SourceCitation(AnswerPlanModel):
    source_url: str
    json_path: str
    snapshot: SnapshotRef


class CompositeCitation(AnswerPlanModel):
    composite: list[SourceCitation] = Field(min_length=1)


class PriceClaim(AnswerPlanModel):
    provider: str
    instance_type: str
    region_native: str
    monthly_usd: float | None = None
    hourly_usd: float | None = None
    snapshot_age_hours: float
    citation: SourceCitation | CompositeCitation
    source_result_index: int = Field(ge=0)


class CandidateClaim(AnswerPlanModel):
    provider: str
    instance_type: str
    monthly_usd: float | None = None
    snapshot_age_hours: float | None = None
    source_result_index: int = Field(ge=0)
    considered_index: int | None = Field(default=None, ge=0)


class UnmetRequirementClaim(AnswerPlanModel):
    provider: str | None = None
    region: str | None = None
    reason: str


class ViewColumn(AnswerPlanModel):
    column_id: str
    label: str | None = None


class ViewSort(AnswerPlanModel):
    column_id: str
    direction: Literal["asc", "desc"] = "asc"


class PlanViewSpec(AnswerPlanModel):
    """The declarative view the agent chose, folded into the AnswerPlan so one
    validator covers both claims and view (TASKS R5/R6). Columns must resolve to
    the column registry and price-bearing cells must bind to validated rows."""

    layout: Literal["table", "grouped"] = "table"
    columns: list[ViewColumn] = Field(min_length=1, max_length=16)
    group_by: str | None = None
    sort: ViewSort | None = None
    source_result_indices: list[int] = Field(default_factory=list, max_length=64)
    # Tier-3 columns the user asked for that the snapshot cannot back. The agent
    # surfaces these as an explicit refusal instead of fabricating them (R7).
    refused_columns: list[str] = Field(default_factory=list, max_length=16)


class AnswerPlan(AnswerPlanModel):
    answer_type: Literal["ranking", "lookup", "missing_data", "stale", "refusal"]
    price_claims: list[PriceClaim] = Field(default_factory=list)
    candidate_claims: list[CandidateClaim] = Field(default_factory=list)
    unmet_requirements: list[UnmetRequirementClaim] = Field(default_factory=list)
    refusal_reason: str | None = None
    view_spec: PlanViewSpec | None = None
