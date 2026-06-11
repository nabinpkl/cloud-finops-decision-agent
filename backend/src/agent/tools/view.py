"""Framework-neutral co-driver tools: ``set_view`` and ``select`` (TASKS R3).

These are the agent's only legal view-state mutations besides requesting a
deterministic ``compare``/``lookup``: declare a view over already-validated
results, and select/highlight verified rows. The tool callables here validate
the *shape* (typed view-spec / selection); the registry + cell-binding
validation that makes "agent-decided but not agent-invented" enforceable lives
in the AnswerPlan view-spec validation (step 3). The backend applies the
returned spec to the backend-authoritative view-state only after that
validation passes.

Like the pricing tool, the body is framework-neutral; each runtime adapter
wraps it in its own tool type. No price/citation/value is written here.
"""

from __future__ import annotations

from typing import Any

from agent.security.untrusted import wrap_tool_result_json
from agent.tools.view_models import SelectionSpec, ViewSpec

SET_VIEW_DESCRIPTION = (
    "Declare how the comparison table should be laid out over results already "
    "returned by compare/lookup. Choose columns from the registered column "
    "vocabulary, optionally group/sort, and list which result rows to show. "
    "This sets layout only; it never writes a price, citation, or instance "
    "match. Every column must resolve to a registered column and every shown "
    "row must come from a validated tool result, or the whole plan is rejected."
)

SELECT_DESCRIPTION = (
    "Annotate the current comparison table: select one or more verified result "
    "rows and optionally mark one as the highlight. Row indices reference the "
    "latest validated compare/lookup result. This is annotation only; it never "
    "writes any value into a row."
)


def run_set_view(**kwargs: Any) -> dict[str, Any]:
    """Validate the view-spec shape and return it as a structured result.

    Raises ``pydantic.ValidationError`` on a malformed spec, which the runtime
    surfaces back to the model as a tool error so it can retry.
    """
    spec = ViewSpec(**kwargs)
    return {"kind": "set_view", "view": spec.model_dump()}


def run_select(**kwargs: Any) -> dict[str, Any]:
    """Validate the selection shape and return it as a structured result."""
    spec = SelectionSpec(**kwargs)
    return {"kind": "select", "selection": spec.model_dump()}


def run_set_view_for_model(**kwargs: Any) -> tuple[str, dict[str, Any]]:
    result = run_set_view(**kwargs)
    return wrap_tool_result_json("set_view", result), result


def run_select_for_model(**kwargs: Any) -> tuple[str, dict[str, Any]]:
    result = run_select(**kwargs)
    return wrap_tool_result_json("select", result), result
