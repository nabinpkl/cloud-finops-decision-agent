from __future__ import annotations

from pathlib import Path

from agent.runtime.prompt import INSTRUCTIONS


def test_agent_prompt_loads_from_root_prompts_directory():
    prompt_path = Path(__file__).resolve().parents[2] / "prompts" / "finops_agent.md"

    assert prompt_path.exists()
    assert INSTRUCTIONS == prompt_path.read_text(encoding="utf-8").strip()
    assert "Every price you state must come from a tool result" in INSTRUCTIONS
    assert "(snapshot Xh old)" in INSTRUCTIONS
    assert "rather than guessing" in INSTRUCTIONS
