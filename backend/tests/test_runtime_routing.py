"""The runtime router (ADR-0012): `AGENT_RUNTIME` selects the adapter and the
import is lazy. "deepagents" (the LangChain adapter) is the default; both runtime
stacks are core dependencies."""

from __future__ import annotations

import pytest

from agent import runtime as runtime_pkg
from app_config import Settings, settings


def test_default_value_is_deepagents():
    assert Settings.model_fields["agent_runtime"].default == "deepagents"


def test_deepagents_selector_returns_deepagents_runtime(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "agent_runtime", "deepagents")
    rt = runtime_pkg.get_runtime()
    assert type(rt).__name__ == "DeepAgentsRuntime"


def test_openai_agents_selector_returns_openai_runtime(monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("agents")
    monkeypatch.setattr(settings, "agent_runtime", "openai_agents")
    rt = runtime_pkg.get_runtime()
    assert type(rt).__name__ == "OpenAIAgentsRuntime"


def test_failed_adapter_import_propagates_not_silent_fallback(
    monkeypatch: pytest.MonkeyPatch,
):
    # If the selected adapter's import fails, get_runtime must surface the error,
    # not quietly hand back the other runtime (which would mask a misconfig).
    monkeypatch.setattr(settings, "agent_runtime", "deepagents")
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):
        if name == "agent.runtime.deepagents" or name.startswith("langchain"):
            raise ImportError("simulated adapter import failure")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ImportError):
        runtime_pkg.get_runtime()
