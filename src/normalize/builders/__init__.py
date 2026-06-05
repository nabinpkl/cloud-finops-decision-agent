"""Per-provider index builders.

Each builder exports a `build(snapshot_dir: Path) -> BuilderOutput` function that
reads the raw JSON files in the snapshot directory and returns the parquet rows
plus a fingerprint of the source structure.

All schema knowledge for the seven providers lives in this package (per
ADR 0002). Gates fetch; builders translate."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from normalize.schema import IndexRow


@dataclass
class BuilderOutput:
    rows: list[IndexRow]
    fingerprint: list[list[str]]
    source_files: list[str] = field(default_factory=list)


class Builder(Protocol):
    def build(self, snapshot_dir: Path) -> BuilderOutput: ...
