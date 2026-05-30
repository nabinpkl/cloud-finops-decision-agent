"""Agent tools registered with the OpenAI Agents SDK (ADR-0009).

The agent's pricing answers ride on the same deterministic functions the HTTP
surface exposes. `compare` wraps `normalize.query.compare` and routes the result
through `api.wire.wire_response`, so the citation translation that protects HTTP
callers (drop the filesystem `store_path`, add a logical `snapshot` ref) also
protects the agent and, transitively, the browser. The agent never sees a path
the user cannot independently verify.

`_run_compare` is the underlying callable so tests can exercise it without going
through the SDK's `FunctionTool` invoke wrapper.
"""

from __future__ import annotations

from typing import Any

from agents import function_tool

from api.wire import wire_response
from normalize.query import compare as _normalize_compare


def _run_compare(
    vcpu: int,
    ram_gb: float,
    region: str,
    family: str = "any",
    providers: list[str] | None = None,
    expand: str = "cheapest",
) -> dict[str, Any]:
    result = _normalize_compare(
        vcpu=vcpu,
        ram_gb=ram_gb,
        region=region,
        family=family,
        providers=providers,
        expand=expand,
    )
    return wire_response(result)


@function_tool
def compare(
    vcpu: int,
    ram_gb: float,
    region: str,
    family: str = "any",
    providers: list[str] | None = None,
    expand: str = "cheapest",
) -> dict[str, Any]:
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
    return _run_compare(
        vcpu=vcpu,
        ram_gb=ram_gb,
        region=region,
        family=family,
        providers=providers,
        expand=expand,
    )
