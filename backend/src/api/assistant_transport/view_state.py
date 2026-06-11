"""Apply validated co-driver results to the backend-authoritative view-state.

The agent never writes view-state directly. It emits ``set_view`` / ``select``
tool results; the backend applies them to ``state['view']`` / ``state['selection']``
here. A ``set_view`` result only mutates ``state['view']`` after passing the SAME
registry + Tier-3-refusal + row-binding validation that gates the AnswerPlan
view-spec (``agent.policy.answer_plan_validation.validate_view_spec_fields``).
A ``select`` result only mutates ``state['selection']`` after its row indices are
bound to validated result rows. An unregistered column, a Tier-3 (refused)
column, or an out-of-range / unbindable row index rejects the whole result and
leaves view-state untouched.

This is the enforcement point for "backend writes state ONLY from validated
results" (ADR-0016 HARD CONSTRAINT): the path that actually mutates the
broadcast view-state runs the full validation, not just a pydantic shape check.
The single owner of view-state is the FastAPI process (TASKS R3).
"""

from __future__ import annotations

from typing import Any

from agent.policy.answer_plan_validation import validate_view_spec_fields


def apply_view_tool_result(
    state: dict[str, Any],
    result: Any,
    result_rows: list[dict[str, Any]] | None = None,
) -> bool:
    """Apply a ``set_view`` or ``select`` tool result to the view-state.

    ``result_rows`` are the rows of the latest validated ``compare``/``lookup``
    tool result; they are the binding target for view/selection row indices.
    Returns True if a mutation was applied. Returns False (and leaves state
    untouched) for anything that is not a well-formed AND validated co-driver
    result, so a stray, malformed, or unvalidated tool result never corrupts the
    backend-authoritative view-state.
    """
    if not isinstance(result, dict):
        return False
    rows = result_rows or []
    kind = result.get("kind")
    if kind == "set_view" and isinstance(result.get("view"), dict):
        return _apply_set_view(state, result["view"], rows)
    if kind == "select" and isinstance(result.get("selection"), dict):
        return _apply_select(state, result["selection"], rows)
    return False


def _apply_set_view(
    state: dict[str, Any],
    view: dict[str, Any],
    rows: list[dict[str, Any]],
) -> bool:
    columns = [
        c["column_id"]
        for c in view.get("columns", [])
        if isinstance(c, dict) and isinstance(c.get("column_id"), str)
    ]
    sort = view.get("sort")
    sort_column = (
        sort.get("column_id")
        if isinstance(sort, dict) and isinstance(sort.get("column_id"), str)
        else None
    )
    indices = [
        idx for idx in view.get("source_result_indices", []) if isinstance(idx, int)
    ]
    refused = [c for c in view.get("refused_columns", []) if isinstance(c, str)]
    violations = validate_view_spec_fields(
        columns=columns,
        group_by=view.get("group_by"),
        sort_column=sort_column,
        source_result_indices=indices,
        # ViewSpec carries refused_columns (Tier-3 the user asked for); the same
        # validator that gates the AnswerPlan path checks they are genuinely
        # Tier-3, so the set_view path and the prose path enforce one rule.
        refused_columns=refused,
        results=rows,
    )
    if violations:
        return False
    state["view"] = view
    return True


def _apply_select(
    state: dict[str, Any],
    selection: dict[str, Any],
    rows: list[dict[str, Any]],
) -> bool:
    raw_rows = [r for r in selection.get("rows", []) if isinstance(r, int)]
    highlight = selection.get("highlight")
    indices = list(raw_rows)
    if isinstance(highlight, int):
        indices.append(highlight)
    # Annotation channel is bound to validated rows too: select/highlight may
    # only reference rows that exist in the latest validated result set.
    for idx in indices:
        if idx < 0 or idx >= len(rows):
            return False
    state["selection"] = {"rows": raw_rows, "highlight": highlight}
    return True
