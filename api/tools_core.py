"""Framework-neutral tool logic (ADR-0012).

The pricing tools' actual work lives here, free of any agent framework. Each
runtime adapter wraps `run_compare` in its own tool type (the OpenAI Agents
SDK's `function_tool` in `api/tools.py`; a LangChain `@tool` in the DeepAgents
adapter), but the body, including the `wire_response` citation translation that
protects every caller, is defined once in this module.

Keeping this here is what lets the citation contract sit *below* both
frameworks: neither adapter can weaken it, because neither owns it.
"""

from __future__ import annotations

from typing import Any

from api.wire import wire_response
from normalize.query import compare as _normalize_compare

COMPARE_DESCRIPTION = (
    "Rank cloud providers by cheapest instance matching a vCPU/RAM spec.\n\n"
    "Match policy from SPEC.md: the chosen candidate satisfies "
    "vcpu_actual >= vcpu AND ram_gb_actual >= ram_gb, smallest first, ties "
    "broken by lower monthly_usd. Returns one representative result per provider "
    "plus a citation block per result; the agent must surface the citation in "
    "prose."
)


def run_compare(
    vcpu: int,
    ram_gb: float,
    region: str,
    family: str = "any",
    providers: list[str] | None = None,
    expand: str = "cheapest",
) -> dict[str, Any]:
    """Underlying callable for the `compare` tool. Routes the deterministic
    `normalize.query.compare` result through `wire_response` so the filesystem
    `store_path` is dropped and a logical `snapshot` ref is added. Tests and
    adapters call this directly, without any framework's invoke wrapper."""
    result = _normalize_compare(
        vcpu=vcpu,
        ram_gb=ram_gb,
        region=region,
        family=family,
        providers=providers,
        expand=expand,
    )
    return wire_response(result)
