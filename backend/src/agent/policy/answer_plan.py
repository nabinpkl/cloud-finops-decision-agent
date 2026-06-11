"""Public interface for structured claim plans.

The implementation is split by seam so future reviews do not mistake the
claim-plan contract for one large god file:

- `answer_plan_models.py`: Pydantic schema emitted by the model.
- `answer_plan_parser.py`: JSON parsing plus tool-result coercion.
- `answer_plan_validation.py`: deterministic claim binding checks.
- `answer_plan_rendering.py`: final prose interpolation.
"""

from __future__ import annotations

from agent.policy.answer_plan_models import (
    AnswerPlan,
    AnswerPlanModel,
    CandidateClaim,
    CompositeCitation,
    PriceClaim,
    SnapshotRef,
    SourceCitation,
    UnmetRequirementClaim,
)
from agent.policy.answer_plan_parser import parse_answer_plan, render_checked_answer_plan
from agent.policy.answer_plan_rendering import render_answer_plan
from agent.policy.answer_plan_validation import validate_answer_plan
from agent.tools.view_models import ViewSpec

__all__ = [
    "AnswerPlan",
    "AnswerPlanModel",
    "CandidateClaim",
    "CompositeCitation",
    "PriceClaim",
    "SnapshotRef",
    "SourceCitation",
    "UnmetRequirementClaim",
    "ViewSpec",
    "parse_answer_plan",
    "render_answer_plan",
    "render_checked_answer_plan",
    "validate_answer_plan",
]
