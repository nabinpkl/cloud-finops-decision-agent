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

__all__ = ["compare", "_compare_for_model", "_run_compare"]


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
