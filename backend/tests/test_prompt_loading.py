from __future__ import annotations

from agent.runtime.prompt import INSTRUCTIONS
from agent.runtime.prompt_assembly import (
    RENDERED_PROMPT_PATH,
    manifest_sources,
    orphan_prompt_sources,
    parse_rendered_blocks,
    render_prompt,
    validate_rendered_prompt,
)


def test_agent_prompt_loads_from_rendered_prompt():
    rendered = RENDERED_PROMPT_PATH.read_text(encoding="utf-8").strip()

    assert INSTRUCTIONS == rendered


def test_rendered_prompt_matches_manifest_sources():
    assert validate_rendered_prompt() == []


def test_prompt_rendering_is_current():
    assert RENDERED_PROMPT_PATH.read_text(encoding="utf-8") == render_prompt()


def test_prompt_parts_have_no_orphans():
    assert orphan_prompt_sources() == []


def test_rendered_prompt_covers_manifest_sources_in_order():
    rendered = RENDERED_PROMPT_PATH.read_text(encoding="utf-8")
    blocks = parse_rendered_blocks(rendered)
    sources = manifest_sources()

    assert [(block.kind, block.rendered_path) for block in blocks] == [
        (source.kind, source.rendered_path) for source in sources
    ]
    assert "<trust_boundaries>" in INSTRUCTIONS
    assert "<anti_prompt_injection>" in INSTRUCTIONS
    assert "<answer_plan_contract>" in INSTRUCTIONS
    assert "Do not write user-facing prose yourself" in INSTRUCTIONS
    assert "source_result_index" in INSTRUCTIONS
