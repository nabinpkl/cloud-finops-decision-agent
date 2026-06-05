"""Repository path constants shared across backend packages."""

from __future__ import annotations

from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = SRC_ROOT.parent
PROJECT_ROOT = BACKEND_ROOT.parent
TAXONOMY_DIR = SRC_ROOT / "normalize" / "taxonomy"
