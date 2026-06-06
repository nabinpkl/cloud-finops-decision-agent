"""The agent's citation system prompt (ADR-0009, ADR-0012).

Both runtime adapters import ``INSTRUCTIONS`` from this neutral module. The text
lives in the repo-root ``prompts/rendered`` directory so runtime behavior reads
one canonical assembled prompt rather than fragmented prompt sources.
"""

from __future__ import annotations

from agent.runtime.prompt_assembly import RENDERED_PROMPT_PATH


INSTRUCTIONS = RENDERED_PROMPT_PATH.read_text(encoding="utf-8").strip()
