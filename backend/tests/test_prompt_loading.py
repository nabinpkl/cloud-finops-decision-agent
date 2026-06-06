from __future__ import annotations

from pathlib import Path

from agent.runtime.prompt import INSTRUCTIONS


def test_agent_prompt_loads_from_root_prompts_directory():
    prompt_path = Path(__file__).resolve().parents[2] / "prompts" / "finops_agent.md"

    assert prompt_path.exists()
    assert INSTRUCTIONS == prompt_path.read_text(encoding="utf-8").strip()
    assert "<trust_boundaries>" in INSTRUCTIONS
    assert "<anti_prompt_injection>" in INSTRUCTIONS
    assert "<answer_plan_contract>" in INSTRUCTIONS
    assert "Do not write\nuser-facing prose yourself" in INSTRUCTIONS
    assert "source_result_index" in INSTRUCTIONS
