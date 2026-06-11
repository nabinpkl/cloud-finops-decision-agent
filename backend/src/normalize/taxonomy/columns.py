"""Load and query normalize/taxonomy/columns.json (TASKS R5).

The registered column vocabulary for agent-decided views. Three tiers:

- Tier-1: cited source fields read straight off a validated compare/lookup
  result row.
- Tier-2: derived columns, deterministic functions of cited Tier-1 inputs, each
  carrying its formula and the inputs it cites (ADR-0007 provenance pattern).
- Tier-3: ``dimensions_not_normalized`` (network bandwidth, cpu generation,
  included storage). Refused, never filled; an explicit ask gets a graceful
  refusal with the closest cited columns offered.

The view-spec validator (agent.policy) enforces that every chosen column
resolves here; an unregistered column rejects the whole plan.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from project_paths import TAXONOMY_DIR

COLUMNS_PATH = TAXONOMY_DIR / "columns.json"


@dataclass(frozen=True)
class ColumnEntry:
    column_id: str
    tier: int
    label: str
    kind: str | None
    price_bearing: bool
    source_field: str | None
    formula: str | None
    cited_inputs: tuple[str, ...]
    refusal_reason: str | None


@lru_cache(maxsize=1)
def _raw() -> dict:
    return json.loads(COLUMNS_PATH.read_text())


@lru_cache(maxsize=1)
def _registry() -> dict[str, ColumnEntry]:
    raw = _raw()
    out: dict[str, ColumnEntry] = {}
    for cid, body in raw.get("tier1", {}).items():
        out[cid] = ColumnEntry(
            column_id=cid,
            tier=1,
            label=body["label"],
            kind=body.get("kind"),
            price_bearing=bool(body.get("price_bearing")),
            source_field=body.get("source_field"),
            formula=None,
            cited_inputs=(),
            refusal_reason=None,
        )
    for cid, body in raw.get("tier2", {}).items():
        out[cid] = ColumnEntry(
            column_id=cid,
            tier=2,
            label=body["label"],
            kind=body.get("kind"),
            price_bearing=bool(body.get("price_bearing")),
            source_field=None,
            formula=body.get("formula"),
            cited_inputs=tuple(body.get("cited_inputs", [])),
            refusal_reason=None,
        )
    for cid, body in raw.get("tier3", {}).items():
        out[cid] = ColumnEntry(
            column_id=cid,
            tier=3,
            label=body["label"],
            kind=None,
            price_bearing=False,
            source_field=None,
            formula=None,
            cited_inputs=(),
            refusal_reason=body.get("reason"),
        )
    return out


def get_column(column_id: str) -> ColumnEntry | None:
    """Return the registry entry for a column id, or None if unregistered."""
    return _registry().get(column_id)


def is_registered(column_id: str) -> bool:
    return column_id in _registry()


def is_refused(column_id: str) -> bool:
    """True for Tier-3 columns the snapshot cannot back (graceful refusal)."""
    entry = get_column(column_id)
    return entry is not None and entry.tier == 3


def all_columns() -> dict[str, ColumnEntry]:
    return dict(_registry())


def cited_columns() -> list[str]:
    """Tier-1 + Tier-2 column ids: the columns an agent may legally show."""
    return [cid for cid, e in _registry().items() if e.tier in (1, 2)]
