from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.runtime.prompt_assembly import prompt_identity
from project_paths import PROJECT_ROOT


MODEL_CONFIG_PATH = PROJECT_ROOT / "config" / "models.yaml"


@dataclass(frozen=True)
class EvalRunIdentity:
    prompt: dict[str, Any]
    model_config: dict[str, str]
    cases: dict[str, str]
    git: dict[str, str | None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "model_config": self.model_config,
            "cases": self.cases,
            "git": self.git,
        }


def build_eval_identity(cases_path: Path) -> EvalRunIdentity:
    resolved_cases_path = _project_path(cases_path)
    return EvalRunIdentity(
        prompt=prompt_identity().to_dict(),
        model_config={
            "path": _relative_project_path(MODEL_CONFIG_PATH),
            "sha256": _sha256_file(MODEL_CONFIG_PATH),
        },
        cases={
            "path": _relative_project_path(resolved_cases_path),
            "sha256": _sha256_cases(resolved_cases_path),
        },
        git={"commit": _git_commit()},
    )


def _sha256_cases(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_dir():
        files = sorted([*path.glob("*.yaml"), *path.glob("*.yml")])
        for file_path in files:
            relative = file_path.relative_to(path).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(file_path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()
    return _sha256_file(path)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _project_path(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def _relative_project_path(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT).as_posix()


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    commit = result.stdout.strip()
    return commit or None
