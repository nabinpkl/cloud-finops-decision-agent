"""Co-driver tools: set_view + select (TASKS R3, ADR-0016).

The agent's only legal view-state mutations besides requesting deterministic
data are: declare a view over validated results and annotate verified rows.
These tests cover the framework-neutral tool callables, the backend-side
application of their results to the view-state, and the guarantee that the
backend never writes a price/citation/value through this channel.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.tools.view import run_select, run_set_view
from agent.tools.view_models import SelectionSpec, ViewSpec
from api.assistant_transport.view_state import apply_view_tool_result


def test_set_view_returns_validated_spec():
    result = run_set_view(
        columns=[{"column_id": "provider"}, {"column_id": "monthly_usd"}],
        layout="table",
        source_result_indices=[0, 1],
    )
    assert result["kind"] == "set_view"
    assert result["view"]["columns"][0]["column_id"] == "provider"
    assert result["view"]["source_result_indices"] == [0, 1]


def test_set_view_rejects_unknown_field():
    # extra='forbid' blocks smuggling a price/value into the view-spec.
    with pytest.raises(ValidationError):
        ViewSpec.model_validate(
            {"columns": [{"column_id": "provider"}], "price": "$1.00"}
        )


def test_set_view_requires_at_least_one_column():
    with pytest.raises(ValidationError):
        ViewSpec(columns=[])


def test_select_returns_validated_selection():
    result = run_select(rows=[0, 2], highlight=0)
    assert result["kind"] == "select"
    assert result["selection"]["rows"] == [0, 2]
    assert result["selection"]["highlight"] == 0


def test_selection_rejects_unknown_field():
    with pytest.raises(ValidationError):
        SelectionSpec.model_validate({"rows": [0], "monthly_usd": 1.0})


def _fresh_state() -> dict:
    return {"view": None, "selection": {"rows": [], "highlight": None}}


def _rows(n: int) -> list[dict]:
    return [{"provider": "aws", "instance_type": f"x{i}"} for i in range(n)]


def test_apply_set_view_mutates_view_state_when_validated():
    # A registered column over a row that binds to a validated result.
    state = _fresh_state()
    result = run_set_view(
        columns=[{"column_id": "provider"}], source_result_indices=[0]
    )
    assert apply_view_tool_result(state, result, _rows(1)) is True
    view = state["view"]
    assert view is not None
    assert view["columns"][0]["column_id"] == "provider"


def test_apply_set_view_rejects_tier3_column():
    # Tier-3 (dimensions_not_normalized) is refused, never filled (ADR-0016).
    state = _fresh_state()
    result = run_set_view(
        columns=[{"column_id": "cpu_generation"}], source_result_indices=[0]
    )
    assert apply_view_tool_result(state, result, _rows(1)) is False
    assert state["view"] is None


def test_apply_set_view_rejects_unregistered_column():
    state = _fresh_state()
    result = run_set_view(
        columns=[{"column_id": "fabricated"}], source_result_indices=[0]
    )
    assert apply_view_tool_result(state, result, _rows(1)) is False
    assert state["view"] is None


def test_apply_set_view_rejects_unbindable_row():
    # source_result_indices must bind to a real validated result row.
    state = _fresh_state()
    result = run_set_view(
        columns=[{"column_id": "provider"}], source_result_indices=[5]
    )
    assert apply_view_tool_result(state, result, _rows(2)) is False
    assert state["view"] is None


def test_apply_set_view_rejects_unregistered_group_by():
    state = _fresh_state()
    result = run_set_view(
        columns=[{"column_id": "provider"}],
        group_by="fabricated",
    )
    assert apply_view_tool_result(state, result, _rows(1)) is False
    assert state["view"] is None


def test_apply_select_mutates_selection_when_rows_bind():
    state = _fresh_state()
    result = run_select(rows=[1], highlight=1)
    assert apply_view_tool_result(state, result, _rows(2)) is True
    assert state["selection"] == {"rows": [1], "highlight": 1}


def test_apply_select_rejects_out_of_range_row():
    # The annotation channel is bound to validated rows too (ADR-0016).
    state = _fresh_state()
    result = run_select(rows=[9], highlight=9)
    assert apply_view_tool_result(state, result, _rows(2)) is False
    assert state["selection"] == {"rows": [], "highlight": None}


def test_apply_ignores_non_codriver_result():
    state = _fresh_state()
    # A compare tool result must never mutate view-state through this channel.
    assert apply_view_tool_result(state, {"results": [{"monthly_usd": 1.0}]}) is False
    assert state["view"] is None
    assert state["selection"] == {"rows": [], "highlight": None}


def test_apply_ignores_malformed_result():
    state = _fresh_state()
    assert apply_view_tool_result(state, "not-a-dict") is False
    assert apply_view_tool_result(state, {"kind": "set_view"}) is False
    assert state["view"] is None
