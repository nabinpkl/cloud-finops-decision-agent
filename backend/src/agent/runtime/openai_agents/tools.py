"""OpenAI Agents SDK tool bindings (ADR-0009, ADR-0012).

This module is part of the OpenAI-agents *adapter*: it wraps the
framework-neutral logic in `agent/tools/pricing.py` with the Agents SDK's
`function_tool` so the OpenAI runtime adapter can call
it. The LangChain adapter binds the same `run_compare` with a LangChain
`@tool` instead; the shared body, and the `wire_response` citation translation
inside it, lives once in `agent.tools.pricing`.

`_run_compare` is re-exported for tests that exercise the callable without
going through the SDK's `FunctionTool` invoke wrapper.
"""

from __future__ import annotations

from agents import function_tool

from agent.security.untrusted import wrap_tool_result_json
from agent.tools.pricing import (
    ExpandMode,
    FamilyName,
    ProviderName,
    run_compare as _run_compare,
)
from agent.tools.view import run_select as _run_select
from agent.tools.view import run_set_view as _run_set_view
from agent.tools.view_models import LayoutKind, SortDirection

__all__ = [
    "compare",
    "set_view",
    "select",
    "_compare_for_model",
    "_run_compare",
]


def _compare_for_model(
    *,
    vcpu: int,
    ram_gb: float,
    region: str,
    family: FamilyName = "any",
    providers: list[ProviderName] | None = None,
    expand: ExpandMode = "cheapest",
) -> str:
    result = _run_compare(
        vcpu=vcpu,
        ram_gb=ram_gb,
        region=region,
        family=family,
        providers=providers,
        expand=expand,
    )
    return wrap_tool_result_json("compare", result)


@function_tool
def compare(
    vcpu: int,
    ram_gb: float,
    region: str,
    family: FamilyName = "any",
    providers: list[ProviderName] | None = None,
    expand: ExpandMode = "cheapest",
) -> str:
    """Rank cloud providers by cheapest instance matching a vCPU/RAM spec.

    Match policy from SPEC.md: the chosen candidate satisfies
    vcpu_actual >= vcpu AND ram_gb_actual >= ram_gb, smallest first, ties broken
    by lower monthly_usd. Returns one representative result per provider plus a
    citation block per result; the agent must surface the citation in prose.

    Args:
        vcpu: Minimum vCPU count.
        ram_gb: Minimum RAM in GB.
        region: Region selector (e.g. "eu-central", "us-east", or a provider
            native id). The query layer maps to per-provider native regions.
        family: Instance family filter ("any", "general-purpose",
            "compute-optimized", "memory-optimized").
        providers: Subset of providers to compare; default is the v0 7-provider
            set (aws, gcp, azure, oracle, vultr, linode, ibm).
        expand: "cheapest" returns one result per provider; "full" also includes
            the per-provider ranked candidate list under `considered`.
    """
    return _compare_for_model(
        vcpu=vcpu,
        ram_gb=ram_gb,
        region=region,
        family=family,
        providers=providers,
        expand=expand,
    )


@function_tool
def set_view(
    column_ids: list[str],
    layout: LayoutKind = "table",
    group_by: str | None = None,
    sort_column: str | None = None,
    sort_direction: SortDirection = "asc",
    source_result_indices: list[int] | None = None,
) -> str:
    """Declare how the comparison table is laid out over validated results.

    The agent owns the view; the deterministic layer owns every value. Choose
    columns from the registered column vocabulary, optionally group/sort, and
    list which result rows to show. This sets layout only; it never writes a
    price, citation, or instance match. Every column must resolve to a
    registered column and every shown row must come from a validated
    compare/lookup result, or the whole plan is rejected.

    Args:
        column_ids: Ordered list of registered column ids to show.
        layout: "table" (flat ranked list) or "grouped" (grouped by group_by).
        group_by: Column id to group rows by when layout is "grouped".
        sort_column: Column id to sort by, or null.
        sort_direction: "asc" or "desc" when sort_column is set.
        source_result_indices: Indices into the latest validated tool result's
            results list that this view should render.
    """
    sort = (
        {"column_id": sort_column, "direction": sort_direction}
        if sort_column is not None
        else None
    )
    return wrap_tool_result_json(
        "set_view",
        _run_set_view(
            columns=[{"column_id": cid} for cid in column_ids],
            layout=layout,
            group_by=group_by,
            sort=sort,
            source_result_indices=source_result_indices or [],
        ),
    )


@function_tool
def select(
    rows: list[int] | None = None,
    highlight: int | None = None,
) -> str:
    """Annotate the comparison table: select verified rows, mark one highlight.

    Row indices reference the latest validated compare/lookup result. This is
    annotation only; it never writes any value into a row.

    Args:
        rows: Result-row indices to select.
        highlight: A single result-row index to emphasize, or null.
    """
    return wrap_tool_result_json(
        "select",
        _run_select(rows=rows or [], highlight=highlight),
    )
