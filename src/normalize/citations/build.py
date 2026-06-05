"""Citation construction for query results."""

from __future__ import annotations

from typing import Any

from normalize.query.models import CitationBlock
from normalize.snapshot_time import snapshot_age_hours


def build_citation(row: dict[str, Any], receipt: dict[str, Any]) -> CitationBlock:
    fetched_at = str(receipt.get("fetched_at", ""))
    return CitationBlock(
        source_url=str(row.get("source_url", "")),
        store_path=str(row.get("store_path", "")),
        json_path=str(row.get("json_path", "")),
        fetched_at=fetched_at,
        age_hours=snapshot_age_hours(fetched_at),
    )
