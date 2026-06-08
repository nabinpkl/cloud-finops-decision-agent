"""Prompt manifest loading, rendering, and coverage checks."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field
from project_paths import PROJECT_ROOT


PROMPTS_ROOT = PROJECT_ROOT / "prompts" / "agents"
PRICE_AGENT_PROMPT_BUNDLE = "price-agent"
INPUT_JUDGE_PROMPT_BUNDLE = "input-judge"
RENDERED_PROMPT_FILENAME = "rendered.system.md"


def prompt_manifest_path(bundle: str) -> Path:
    """Return the manifest path for a named prompt bundle."""

    return PROMPTS_ROOT / bundle / "manifest.yaml"


def prompt_rendered_path(bundle: str) -> Path:
    """Return the rendered runtime prompt path for a named prompt bundle."""

    return PROMPTS_ROOT / bundle / RENDERED_PROMPT_FILENAME


PRICE_AGENT_MANIFEST_PATH = prompt_manifest_path(PRICE_AGENT_PROMPT_BUNDLE)
PRICE_AGENT_RENDERED_PROMPT_PATH = prompt_rendered_path(PRICE_AGENT_PROMPT_BUNDLE)
INPUT_JUDGE_MANIFEST_PATH = prompt_manifest_path(INPUT_JUDGE_PROMPT_BUNDLE)
INPUT_JUDGE_RENDERED_PROMPT_PATH = prompt_rendered_path(INPUT_JUDGE_PROMPT_BUNDLE)

_BEGIN_RE = re.compile(
    r"<!-- BEGIN (?P<kind>prompt_(?:part|example)): (?P<path>[^ ]+) "
    r"sha256:(?P<sha256>[0-9a-f]{64}) -->\n"
    r"(?P<content>.*?)"
    r"<!-- END (?P=kind): (?P=path) -->",
    re.DOTALL,
)


class PromptManifest(BaseModel):
    """Prompt composition manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(gt=0)
    name: str
    description: str
    release_notes: str = Field(min_length=1)
    parts: list[str] = Field(min_length=1)
    examples: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class PromptSource:
    """One source file included in the rendered prompt."""

    kind: Literal["prompt_part", "prompt_example"]
    path: Path
    rendered_path: str
    content: str
    sha256: str


@dataclass(frozen=True)
class RenderedBlock:
    """One marked block in the rendered prompt."""

    kind: str
    rendered_path: str
    content: str
    sha256: str


@dataclass(frozen=True)
class PromptIdentity:
    """Immutable identity for the prompt artifact used by evals and traces."""

    name: str
    version: int
    release_notes: str
    manifest_path: str
    manifest_sha256: str
    rendered_path: str
    rendered_sha256: str
    sources: list[dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "release_notes": self.release_notes,
            "manifest_path": self.manifest_path,
            "manifest_sha256": self.manifest_sha256,
            "rendered_path": self.rendered_path,
            "rendered_sha256": self.rendered_sha256,
            "sources": self.sources,
        }


def load_manifest(path: Path = PRICE_AGENT_MANIFEST_PATH) -> PromptManifest:
    """Load and validate the prompt manifest."""

    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"prompt manifest must be a YAML mapping: {path}")
    return PromptManifest.model_validate(loaded)


def manifest_sources(path: Path = PRICE_AGENT_MANIFEST_PATH) -> list[PromptSource]:
    """Return manifest sources in runtime render order."""

    manifest = load_manifest(path)
    manifest_dir = path.parent
    sources: list[PromptSource] = []
    for relative_path in manifest.parts:
        sources.append(_source_from_path("prompt_part", manifest_dir / relative_path))
    for relative_path in manifest.examples:
        sources.append(_source_from_path("prompt_example", manifest_dir / relative_path))
    return sources


def render_prompt(path: Path = PRICE_AGENT_MANIFEST_PATH) -> str:
    """Render the manifest into one canonical system prompt."""

    manifest = load_manifest(path)
    sections = [
        f"<!-- Rendered from {_relative_project_path(path)}. Do not edit directly. -->",
        f"<!-- prompt_name: {manifest.name} -->",
        f"<!-- prompt_version: {manifest.version} -->",
        f"<!-- prompt_release_notes: {manifest.release_notes} -->",
        f"<!-- description: {manifest.description} -->",
    ]
    sections.extend(_render_source(source) for source in manifest_sources(path))
    return "\n\n".join(sections).rstrip() + "\n"


def render_prompt_bundle(bundle: str) -> str:
    """Render a named prompt bundle."""

    return render_prompt(prompt_manifest_path(bundle))


def write_rendered_prompt(
    manifest_path: Path = PRICE_AGENT_MANIFEST_PATH,
    rendered_path: Path = PRICE_AGENT_RENDERED_PROMPT_PATH,
) -> Path:
    """Write the canonical rendered system prompt."""

    rendered_path.parent.mkdir(parents=True, exist_ok=True)
    rendered_path.write_text(render_prompt(manifest_path), encoding="utf-8")
    return rendered_path


def write_rendered_prompt_bundle(bundle: str) -> Path:
    """Write the rendered artifact for a named prompt bundle."""

    return write_rendered_prompt(
        manifest_path=prompt_manifest_path(bundle),
        rendered_path=prompt_rendered_path(bundle),
    )


def parse_rendered_blocks(rendered: str) -> list[RenderedBlock]:
    """Parse coverage markers from a rendered prompt."""

    return [
        RenderedBlock(
            kind=match.group("kind"),
            rendered_path=match.group("path"),
            content=match.group("content"),
            sha256=match.group("sha256"),
        )
        for match in _BEGIN_RE.finditer(rendered)
    ]


def validate_rendered_prompt(
    manifest_path: Path = PRICE_AGENT_MANIFEST_PATH,
    rendered_path: Path = PRICE_AGENT_RENDERED_PROMPT_PATH,
) -> list[str]:
    """Return prompt coverage and drift violations."""

    violations: list[str] = []
    sources = manifest_sources(manifest_path)
    rendered = rendered_path.read_text(encoding="utf-8")
    blocks = parse_rendered_blocks(rendered)
    if len(blocks) != len(sources):
        violations.append(
            f"rendered prompt has {len(blocks)} marked blocks but manifest has {len(sources)} sources"
        )

    for index, source in enumerate(sources):
        if index >= len(blocks):
            violations.append(f"missing rendered block for {source.rendered_path}")
            continue
        block = blocks[index]
        if block.kind != source.kind:
            violations.append(f"{source.rendered_path} rendered as {block.kind}, expected {source.kind}")
        if block.rendered_path != source.rendered_path:
            violations.append(
                f"rendered block {index} is {block.rendered_path}, expected {source.rendered_path}"
            )
        if block.sha256 != source.sha256:
            violations.append(f"{source.rendered_path} sha256 marker is stale")
        if block.content != _content_for_marker(source.content):
            violations.append(f"{source.rendered_path} rendered content does not match source")

    return violations


def validate_rendered_prompt_bundle(bundle: str) -> list[str]:
    """Return drift violations for a named prompt bundle."""

    return validate_rendered_prompt(
        manifest_path=prompt_manifest_path(bundle),
        rendered_path=prompt_rendered_path(bundle),
    )


def orphan_prompt_sources(manifest_path: Path = PRICE_AGENT_MANIFEST_PATH) -> list[Path]:
    """Return editable prompt source files not listed in the manifest."""

    listed = {source.path.resolve() for source in manifest_sources(manifest_path)}
    prompts_dir = manifest_path.parent
    candidates = [
        path
        for path in prompts_dir.rglob("*.md")
        if not path.name.startswith("rendered.") and path.name != "README.md"
    ]
    return sorted(path for path in candidates if path.resolve() not in listed)


def orphan_prompt_sources_bundle(bundle: str) -> list[Path]:
    """Return editable prompt source files not listed in a named bundle manifest."""

    return orphan_prompt_sources(prompt_manifest_path(bundle))


def prompt_identity(
    manifest_path: Path = PRICE_AGENT_MANIFEST_PATH,
    rendered_path: Path = PRICE_AGENT_RENDERED_PROMPT_PATH,
) -> PromptIdentity:
    """Return the prompt release metadata and immutable rendered artifact hash."""

    manifest = load_manifest(manifest_path)
    sources = manifest_sources(manifest_path)
    return PromptIdentity(
        name=manifest.name,
        version=manifest.version,
        release_notes=manifest.release_notes,
        manifest_path=_relative_project_path(manifest_path),
        manifest_sha256=_sha256_file(manifest_path),
        rendered_path=_relative_project_path(rendered_path),
        rendered_sha256=_sha256_file(rendered_path),
        sources=[
            {
                "kind": source.kind,
                "path": _relative_project_path(source.path),
                "sha256": source.sha256,
            }
            for source in sources
        ],
    )


def prompt_identity_bundle(bundle: str) -> PromptIdentity:
    """Return immutable identity for a named prompt bundle."""

    return prompt_identity(
        manifest_path=prompt_manifest_path(bundle),
        rendered_path=prompt_rendered_path(bundle),
    )


def price_agent_prompt_identity() -> PromptIdentity:
    """Return immutable identity for the main pricing agent prompt."""

    return prompt_identity_bundle(PRICE_AGENT_PROMPT_BUNDLE)


def input_judge_prompt_identity() -> PromptIdentity:
    """Return immutable identity for the mandatory input judge prompt."""

    return prompt_identity_bundle(INPUT_JUDGE_PROMPT_BUNDLE)


def _source_from_path(kind: Literal["prompt_part", "prompt_example"], path: Path) -> PromptSource:
    resolved_path = path.resolve()
    content = path.read_text(encoding="utf-8")
    rendered_path = resolved_path.relative_to(PROJECT_ROOT / "prompts").as_posix()
    return PromptSource(
        kind=kind,
        path=resolved_path,
        rendered_path=rendered_path,
        content=content,
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )


def _render_source(source: PromptSource) -> str:
    return (
        f"<!-- BEGIN {source.kind}: {source.rendered_path} sha256:{source.sha256} -->\n"
        f"{_content_for_marker(source.content)}"
        f"<!-- END {source.kind}: {source.rendered_path} -->"
    )


def _content_for_marker(content: str) -> str:
    return content if content.endswith("\n") else content + "\n"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative_project_path(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT).as_posix()
