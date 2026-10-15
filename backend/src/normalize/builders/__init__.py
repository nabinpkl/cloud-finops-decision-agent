"""Per-provider index builders.

Each builder exports a `build(snapshot_dir: Path) -> BuilderOutput` function that
reads the snapshot's raw provider responses (listed by its receipt, see
normalize.snapshot_files) and returns the parquet rows plus a fingerprint of the
source structure.

Builders also account for what they skipped. A *candidate* is a record our
policy wants priced (say, a Linux on-demand VM SKU). A candidate that cannot be
priced because a field it needs is absent or empty counts against that field in
`missing_fields`; the index gate fails when those counts cross a share of the
candidates, which is how an upstream rename shows up. Records our policy skips on
purpose (Windows, spot, a family outside v0) are neither candidates nor missing.

All schema knowledge for the seven providers lives in this package (per
ADR 0002). Ingest fetches; builders translate."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from normalize.schema import IndexRow


@dataclass
class BuilderOutput:
    rows: list[IndexRow]
    fingerprint: list[list[str]]
    source_files: list[str]
    candidates: int
    missing_fields: dict[str, int]


class Builder(Protocol):
    def build(self, snapshot_dir: Path) -> BuilderOutput: ...


class SkipTally:
    """Counts candidates and the fields that kept a candidate from being priced."""

    def __init__(self) -> None:
        self.candidates = 0
        self.missing: Counter[str] = Counter()

    def candidate(self) -> None:
        self.candidates += 1

    def missing_field(self, name: str) -> None:
        self.missing[name] += 1

    def output(self, *, rows: list[IndexRow], fingerprint: list[list[str]], source_files: list[str]) -> BuilderOutput:
        return BuilderOutput(
            rows=rows,
            fingerprint=fingerprint,
            source_files=source_files,
            candidates=self.candidates,
            missing_fields=dict(sorted(self.missing.items())),
        )


def merged_fingerprint(parts: list[list[list[str]]]) -> list[list[str]]:
    """One fingerprint across several raw files (pages, regions) of a snapshot."""
    return [list(entry) for entry in sorted({tuple(e) for part in parts for e in part})]
