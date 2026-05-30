"""The server-side agent runtime (ADR-0009).

The loop runs in this process on the OpenAI Agents SDK. The model is built
against an OpenAI-compatible base URL (Chat Completions, not Responses) so the
provider is a config knob, not a vendor lock: pointing `PROVIDER_BASE_URL` at
OpenAI, an Anthropic compat endpoint, OpenRouter, or a local server is a .env
change. The Chat Completions model is required because most non-OpenAI compatible
endpoints do not implement the Responses API.

Tools live in `api/tools.py` and are registered on the Agent here; the streaming
endpoint that drives this agent is `api/transport.py`.
"""

from __future__ import annotations

from agents import Agent, AsyncOpenAI, OpenAIChatCompletionsModel

from api.config import settings
from api.tools import compare as compare_tool

INSTRUCTIONS = (
    "You are a cloud FinOps pricing agent. Every price you state must come from a "
    "tool result and carry its citation; never quote a price from memory. Append "
    "the snapshot age inline to each price as '(snapshot Xh old)'. If any cited "
    "snapshot is over 24 hours old, mark the answer stale and offer a refetch. "
    "When the data does not cover what was asked, say so plainly rather than "
    "guessing."
)


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
