"""UTC timestamp helpers for provider snapshots."""

from __future__ import annotations

from datetime import datetime, timezone


def parse_fetched_at(fetched_at: str) -> datetime:
    """Parse a receipt `fetched_at` timestamp as timezone-aware UTC."""
    return datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))


def snapshot_age_hours(fetched_at: str) -> float:
    """Return age in hours for an ISO 8601 UTC receipt timestamp."""
    if not fetched_at:
        return float("nan")
    parsed = parse_fetched_at(fetched_at)
    return (datetime.now(timezone.utc) - parsed).total_seconds() / 3600
