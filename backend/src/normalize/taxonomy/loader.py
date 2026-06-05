"""Load and query normalize/taxonomy/{families,regions}.json.

Provides cheap, indexed lookups used by per-provider builders:
- classify_family(provider, instance_type) -> family slug or "unclassified"
- canonical_region(provider, native_code)  -> canonical bucket or None
- native_region(provider, canonical_bucket) -> native code or None
- families_for_provider(provider)          -> {family: [prefixes]}
"""

from __future__ import annotations

import json
from functools import lru_cache

from project_paths import TAXONOMY_DIR

FAMILIES_PATH = TAXONOMY_DIR / "families.json"
REGIONS_PATH = TAXONOMY_DIR / "regions.json"

UNCLASSIFIED = "unclassified"


@lru_cache(maxsize=1)
def _families_raw() -> dict:
    return json.loads(FAMILIES_PATH.read_text())


@lru_cache(maxsize=1)
def _regions_raw() -> dict:
    return json.loads(REGIONS_PATH.read_text())


@lru_cache(maxsize=1)
def _prefix_table() -> list[tuple[str, str, str]]:
    """Flattened (provider, prefix_lowercase, family) tuples. Longest prefix first
    so e.g. 'g6-nanode' beats 'g6' if both were ever listed."""
    out: list[tuple[str, str, str]] = []
    for family, body in _families_raw().items():
        if family.startswith("_"):
            continue
        for member in body.get("members", []):
            provider = member["provider"]
            for prefix in member.get("prefix", []):
                out.append((provider, prefix.lower(), family))
    out.sort(key=lambda row: -len(row[1]))
    return out


def classify_family(provider: str, instance_type: str) -> str:
    """Return the taxonomy family slug for an instance type, or UNCLASSIFIED."""
    needle = instance_type.lower()
    for p, prefix, family in _prefix_table():
        if p == provider and needle.startswith(prefix):
            return family
    return UNCLASSIFIED


def families_for_provider(provider: str) -> dict[str, list[str]]:
    """Return {family_slug: [prefixes]} for one provider. Families with empty
    prefix lists are returned too (so coverage checks can see deliberate gaps)."""
    out: dict[str, list[str]] = {}
    for family, body in _families_raw().items():
        if family.startswith("_"):
            continue
        for member in body.get("members", []):
            if member["provider"] == provider:
                out[family] = list(member.get("prefix", []))
    return out


def canonical_region(provider: str, native_code: str) -> str | None:
    for canonical, body in _regions_raw().items():
        if canonical.startswith("_"):
            continue
        providers = body.get("providers", {})
        if providers.get(provider) == native_code:
            return canonical
    return None


def native_region(provider: str, canonical_bucket: str) -> str | None:
    body = _regions_raw().get(canonical_bucket)
    if not body:
        return None
    return body.get("providers", {}).get(provider)
