"""Apply validated co-driver results to the backend-authoritative view-state.

The agent never writes view-state directly. It emits ``set_view`` / ``select``
tool results; the backend applies them to ``state['view']`` / ``state['selection']``
here, and only from results that already passed shape validation in the tool
callable. Step 3 extends the gate with registry + cell-binding validation so an
unregistered column or an unbindable row cannot land in state.

This keeps the co-driver rule (TASKS R3) on the backend side of the wire: the
single owner of view-state is the FastAPI process.
"""

from __future__ import annotations

from typing import Any


def apply_view_tool_result(state: dict[str, Any], result: Any) -> bool:
    """Apply a ``set_view`` or ``select`` tool result to the view-state.

    Returns True if a mutation was applied. Ignores anything that is not a
    well-formed co-driver result, so a stray tool result never corrupts state.
    """
    if not isinstance(result, dict):
        return False
    kind = result.get("kind")
    if kind == "set_view" and isinstance(result.get("view"), dict):
        state["view"] = result["view"]
        return True
    if kind == "select" and isinstance(result.get("selection"), dict):
        selection = result["selection"]
        state["selection"] = {
            "rows": selection.get("rows", []),
            "highlight": selection.get("highlight"),
        }
        return True
    return False
