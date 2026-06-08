from __future__ import annotations

import hashlib

from agent.runtime.prompt import INSTRUCTIONS
from agent.runtime.prompt_assembly import (
    RENDERED_PROMPT_PATH,
    load_manifest,
    manifest_sources,
    orphan_prompt_sources,
    parse_rendered_blocks,
    prompt_identity,
    render_prompt,
    validate_rendered_prompt,
)


def test_agent_prompt_loads_from_rendered_prompt():
    rendered = RENDERED_PROMPT_PATH.read_text(encoding="utf-8").strip()

    assert INSTRUCTIONS == rendered


def test_prompt_manifest_has_human_version_metadata():
    manifest = load_manifest()

    assert manifest.version > 0
    assert manifest.release_notes


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
    manifest = load_manifest()

    assert [(block.kind, block.rendered_path) for block in blocks] == [
        (source.kind, source.rendered_path) for source in sources
    ]
    assert f"<!-- prompt_name: {manifest.name} -->" in rendered
    assert f"<!-- prompt_version: {manifest.version} -->" in rendered
    assert f"<!-- prompt_release_notes: {manifest.release_notes} -->" in rendered
    assert "<trust_boundaries>" in INSTRUCTIONS
    assert "<anti_prompt_injection>" in INSTRUCTIONS
    assert "<answer_plan_contract>" in INSTRUCTIONS
    assert "Do not write user-facing prose yourself" in INSTRUCTIONS
    assert "source_result_index" in INSTRUCTIONS


def test_prompt_identity_matches_rendered_artifact_and_sources():
    identity = prompt_identity()
    rendered = RENDERED_PROMPT_PATH.read_bytes()

    assert identity.name == load_manifest().name
    assert identity.version == load_manifest().version
    assert identity.rendered_sha256 == hashlib.sha256(rendered).hexdigest()
    assert [source["sha256"] for source in identity.sources] == [
        source.sha256 for source in manifest_sources()
    ]
