"""Agent-runtime selection (ADR-0012).

`get_runtime()` returns the `AgentRuntime` implementation chosen by
`settings.agent_runtime` (env `AGENT_RUNTIME`); "deepagents" (the LangChain
adapter) is the default. The framework-specific adapter is imported lazily
inside the branch, so importing this package is cheap and flipping the runtime
is one env var.

The neutral port types are re-exported here so callers import everything they
need from `api.runtime`.
"""

from __future__ import annotations

from api.config import settings
from api.runtime.types import (
    AgentRuntime,
    Emitter,
    RunUsage,
    Turn,
    TurnTokenCapExceeded,
)

__all__ = [
    "AgentRuntime",
    "Emitter",
    "RunUsage",
    "Turn",
    "TurnTokenCapExceeded",
    "get_runtime",
]


def get_runtime() -> AgentRuntime:
    if settings.agent_runtime == "deepagents":
        from api.runtime.deepagents import DeepAgentsRuntime

        return DeepAgentsRuntime()
    from api.runtime.openai_agents import OpenAIAgentsRuntime

    return OpenAIAgentsRuntime()
