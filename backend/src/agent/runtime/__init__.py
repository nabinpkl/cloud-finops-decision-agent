"""Agent-runtime selection (ADR-0012).

`get_runtime()` returns the `AgentRuntime` implementation chosen by
`settings.agent_runtime` (env `AGENT_RUNTIME`); "langchain" (the LangChain
adapter) is the default. The framework-specific adapter is imported lazily
inside the branch, so importing this package is cheap and flipping the runtime
is one env var.

The neutral port types are re-exported here so callers import everything they
need from `agent.runtime`.
"""

from __future__ import annotations

from agent.runtime.types import (
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
    from app_config import settings

    if settings.agent_runtime == "langchain":
        from agent.runtime.langchain import LangChainRuntime

        return LangChainRuntime()
    from agent.runtime.openai_agents import OpenAIAgentsRuntime

    return OpenAIAgentsRuntime()
