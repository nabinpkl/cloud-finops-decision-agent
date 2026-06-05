"""The agent's citation system prompt (ADR-0009, ADR-0012).

Both runtime adapters import ``INSTRUCTIONS`` from this neutral module. The text
lives in the repo-root ``prompts/`` directory so prompt changes are reviewable as
prompt assets rather than hidden in Python code.
"""

from __future__ import annotations

from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


INSTRUCTIONS = (_repo_root() / "prompts" / "finops_agent.md").read_text(
    encoding="utf-8"
).strip()
