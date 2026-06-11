"""Declarative view-spec and selection models for the co-driver tools (R3/R5).

The agent owns the *view*; the deterministic layer owns every *value*
(ADR-0016, ADR-0017, TASKS R3). These models are the typed shape the agent
emits through the ``set_view`` and ``select`` tools. They carry layout intent
only: which columns to show, how to group/sort, and which validated rows to
annotate. No price, citation, or instance match is ever written here; those
live in the ``compare``/``lookup`` tool results and are bound at validation
time (step 3, AnswerPlan view-spec validation).

Frozen + ``extra='forbid'`` so the agent cannot smuggle unknown fields, exactly
like the AnswerPlan claim models.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

LayoutKind = Literal["table", "grouped"]
SortDirection = Literal["asc", "desc"]


class ColumnSpec(BaseModel):
    """One column the agent chose to show.

    ``column_id`` must resolve to a registered column (Tier-1 source field or
    Tier-2 derived formula) at validation time (step 3). A label override is
    presentation-only.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    column_id: str = Field(max_length=64)
    label: str | None = Field(default=None, max_length=64)


class SortSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    column_id: str = Field(max_length=64)
    direction: SortDirection = "asc"


class ViewSpec(BaseModel):
    """A declarative view over the deterministic result set.

    The backend renders this; it never trusts a client-supplied view. The agent
    composes it from registered columns and the validated rows produced by its
    ``compare``/``lookup`` calls (ADR-0017 agent-composed shapes).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    layout: LayoutKind = "table"
    columns: list[ColumnSpec] = Field(min_length=1, max_length=16)
    group_by: str | None = Field(default=None, max_length=64)
    sort: SortSpec | None = None
    # Indices into the latest validated tool result's ``results`` list. Every row
    # the view shows must bind to a real result row (enforced in step 3).
    source_result_indices: list[int] = Field(default_factory=list, max_length=64)
    # Tier-3 columns the user asked for that the snapshot cannot back. The agent
    # surfaces these as an explicit refusal instead of fabricating them (R7).
    # Validated to be genuinely Tier-3, then rendered as a refusal, never filled.
    refused_columns: list[str] = Field(default_factory=list, max_length=16)


class SelectionSpec(BaseModel):
    """Annotation over validated result rows: selection + a single highlight.

    Pure annotation channel (TASKS R3): the agent may select/highlight a
    verified row, never write a value into it. Row indices reference the latest
    validated tool result.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rows: list[int] = Field(default_factory=list, max_length=64)
    highlight: int | None = None
