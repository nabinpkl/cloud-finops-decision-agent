"""The server-side agent runtime (ADR-0009).

The loop runs in this process on the OpenAI Agents SDK. The model is built
against an OpenAI-compatible base URL (Chat Completions, not Responses) so the
provider is a config knob, not a vendor lock: pointing `PROVIDER_BASE_URL` at
OpenAI, an Anthropic compat endpoint, OpenRouter, or a local server is a .env
change. The Chat Completions model is required because most non-OpenAI compatible
endpoints do not implement the Responses API.

Tools live in this package and are registered on the Agent here; the streaming
endpoint that drives this agent is `api/assistant_transport/routes.py`.
"""

from __future__ import annotations

from agents import Agent, OpenAIChatCompletionsModel
from openai import AsyncOpenAI

from app_config import settings
from agent.runtime.prompt import INSTRUCTIONS
from agent.runtime.openai_agents.tools import compare as compare_tool


def build_model() -> OpenAIChatCompletionsModel:
    missing = [
        name
        for name, value in (
            ("PROVIDER_BASE_URL", settings.provider_base_url),
            ("PROVIDER_API_KEY", settings.provider_api_key),
            ("MODEL_NAME", settings.model_name),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "agent model is not configured: set "
            + ", ".join(missing)
            + " in .env (see .env.example)."
        )

    client = AsyncOpenAI(
        base_url=settings.provider_base_url,
        api_key=settings.provider_api_key,
    )
    return OpenAIChatCompletionsModel(model=settings.model_name, openai_client=client)


def build_agent() -> Agent:
    return Agent(
        name="finops",
        instructions=INSTRUCTIONS,
        model=build_model(),
        tools=[compare_tool],
    )
