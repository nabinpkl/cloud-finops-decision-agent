"""LangChain runtime adapter (ADR-0012, AGENT_RUNTIME=langchain).

Implements the `AgentRuntime` port over the LangChain stack. The harness is the
lean `langchain.agents.create_agent` with exactly one tool (`compare`), mirroring
the OpenAI Agents agent one-for-one so the two runtimes are a fair A/B: same
single tool, same citation prompt, honest per-turn token accounting.
All LangChain types stay inside this module. Transport sees only `Turn`,
`Emitter`, `RunUsage`, and `TurnTokenCapExceeded`.

Mapping (verified against langchain 1.3 / langgraph 1.2):
- `astream(..., stream_mode=["messages","updates"])`.
- `messages` mode -> `AIMessageChunk`: stream text via `emit.text_delta`.
- `updates` model node -> `AIMessage.tool_calls`: `emit.tool_call`.
- `updates` tools node -> `ToolMessage`: `emit.tool_result` with the `.artifact`
  dict (the tool returns content_and_artifact, so the structured result reaches
  the frontend intact while the model reads the JSON in `content`).
- Per-turn token cap and usage accounting ride on a `CapMiddleware.after_model`
  hook reading `usage_metadata`; the adapter mirrors its totals into `RunUsage`
  in a `finally` so a turn aborted by the cap still reports what it spent.
"""

from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware
from langchain.agents.structured_output import ProviderStrategy
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI

from app_config import settings
from app_config.model_config import model_config as llm_model_config
from agent.policy.answer_plan import AnswerPlan
from agent.runtime.types import Emitter, RunUsage, Turn, TurnTokenCapExceeded
from agent.runtime.usage import usage_delta
from agent.runtime.prompt import INSTRUCTIONS
from agent.tools.pricing import (
    COMPARE_DESCRIPTION,
    CompareToolArgs,
    run_compare_for_model,
)
from agent.tools.view import (
    SELECT_DESCRIPTION,
    SET_VIEW_DESCRIPTION,
    run_select_for_model,
    run_set_view_for_model,
)
from agent.tools.view_models import SelectionSpec, ViewSpec


def _compare_tool() -> StructuredTool:
    """Bind the neutral `run_compare` as a LangChain tool. `content_and_artifact`
    puts the JSON the model reads (to cite from) in the message content and the
    structured dict the frontend renders in the artifact."""

    def compare(
        vcpu: int,
        ram_gb: float,
        region: str,
        family: str = "any",
        providers: list[str] | None = None,
        expand: str = "cheapest",
    ) -> tuple[str, dict[str, Any]]:
        return run_compare_for_model(
            vcpu=vcpu,
            ram_gb=ram_gb,
            region=region,
            family=family,
            providers=providers,
            expand=expand,
        )

    return StructuredTool.from_function(
        compare,
        name="compare",
        description=COMPARE_DESCRIPTION,
        args_schema=CompareToolArgs,
        response_format="content_and_artifact",
    )


def _set_view_tool() -> StructuredTool:
    """Bind the neutral `run_set_view` co-driver tool (TASKS R3)."""

    def set_view(
        columns: list[dict[str, Any]],
        layout: str = "table",
        group_by: str | None = None,
        sort: dict[str, Any] | None = None,
        source_result_indices: list[int] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        return run_set_view_for_model(
            columns=columns,
            layout=layout,
            group_by=group_by,
            sort=sort,
            source_result_indices=source_result_indices or [],
        )

    return StructuredTool.from_function(
        set_view,
        name="set_view",
        description=SET_VIEW_DESCRIPTION,
        args_schema=ViewSpec,
        response_format="content_and_artifact",
    )


def _select_tool() -> StructuredTool:
    """Bind the neutral `run_select` annotation tool (TASKS R3)."""

    def select(
        rows: list[int] | None = None,
        highlight: int | None = None,
    ) -> tuple[str, dict[str, Any]]:
        return run_select_for_model(rows=rows or [], highlight=highlight)

    return StructuredTool.from_function(
        select,
        name="select",
        description=SELECT_DESCRIPTION,
        args_schema=SelectionSpec,
        response_format="content_and_artifact",
    )


def _build_model() -> ChatOpenAI:
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

    # Output cap rides in extra_body as `max_tokens`, NOT the typed `max_tokens=`
    # kwarg. langchain_openai rewrites the typed kwarg to the OpenAI-newer
    # `max_completion_tokens`, a param name no OpenRouter provider advertises in
    # its supported_parameters. With `provider.require_parameters: true` (set in
    # config/models.yaml so the route only hits providers honoring our tool
    # schemas), OpenRouter then filters out every provider and 404s with "No
    # endpoints found that can handle the requested parameters." Sending the
    # legacy `max_tokens` name that every provider lists keeps the route non-empty.
    extra_body = llm_model_config.main.request.extra_body.as_request_body()
    extra_body["max_tokens"] = llm_model_config.main.request.max_tokens
    kwargs: dict[str, Any] = dict(
        base_url=settings.provider_base_url,
        api_key=settings.provider_api_key,
        model=settings.model_name,
        disable_streaming=llm_model_config.main.request.disable_streaming,
        stream_usage=llm_model_config.main.request.stream_usage,
        use_responses_api=llm_model_config.main.request.use_responses_api,
        extra_body=extra_body,
    )
    return ChatOpenAI(**kwargs)


class CapMiddleware(AgentMiddleware):
    """Per-turn token cap (ADR-0011 seam [5]) for the langchain runtime, the
    analog of the Agents SDK `BudgetHooks`. Accumulates `usage_metadata` after
    every model call and raises the neutral `TurnTokenCapExceeded` once the
    cumulative total crosses the cap. Counts on `self`, one instance per run."""

    def __init__(self, cap: int) -> None:
        super().__init__()
        self.cap = cap
        self.usage = RunUsage()

    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        messages = state.get("messages") if isinstance(state, dict) else None
        last = messages[-1] if messages else None
        usage = getattr(last, "usage_metadata", None)
        if usage:
            delta = usage_delta(usage)
            self.usage.add_call(
                input_tokens=delta.input_tokens,
                output_tokens=delta.output_tokens,
                total_tokens=delta.total_tokens,
                reasoning_tokens=delta.reasoning_tokens,
                cached_input_tokens=delta.cached_input_tokens,
            )
        if self.usage.total >= self.cap:
            raise TurnTokenCapExceeded(
                input_tokens=self.usage.input_tokens,
                output_tokens=self.usage.output_tokens,
                total_tokens=self.usage.total,
                reasoning_tokens=self.usage.reasoning_tokens,
                cap=self.cap,
            )
        return None


class ToolFirstMiddleware(AgentMiddleware):
    """Force the deterministic pricing tool before any structured final answer."""

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        has_tool_result = any(isinstance(message, ToolMessage) for message in request.messages)
        if has_tool_result:
            return await handler(request)
        tool_first_request = request.override(
            response_format=None,
            tool_choice="required",
        )
        return await handler(tool_first_request)


class LangChainRuntime:
    """`AgentRuntime` implementation backed by langchain's `create_agent`."""

    async def run(self, turns: list[Turn], emit: Emitter, usage: RunUsage) -> None:
        cap = CapMiddleware(settings.turn_token_cap)
        # list[Any]: create_agent wants a homogeneous Sequence[AgentMiddleware[...]],
        # but the two middlewares carry different generic params, which the type
        # checker will not unify. They are both AgentMiddleware at runtime.
        middleware: list[Any] = [
            ToolFirstMiddleware(),
            cap,
            ModelCallLimitMiddleware(
                run_limit=settings.max_turns_per_run, exit_behavior="end"
            ),
        ]
        agent = create_agent(
            model=_build_model(),
            tools=[_compare_tool(), _set_view_tool(), _select_tool()],
            system_prompt=INSTRUCTIONS,
            middleware=middleware,
            response_format=ProviderStrategy(
                AnswerPlan,
                strict=llm_model_config.main.structured_output.strict,
            ),
        )
        # LangChain's create_agent returns a graph whose `astream(input=...)`
        # type references a private `_InputAgentState`. The runtime contract is
        # the public messages-state shape below; keep the private type out of
        # our app code and type this adapter boundary as Any.
        agent_input: Any = {
            "messages": [{"role": t.role, "content": t.content} for t in turns]
        }
        try:
            async for mode, chunk in agent.astream(
                input=agent_input, stream_mode=["messages", "updates"]
            ):
                if mode == "messages":
                    # Provider-native structured output streams raw JSON chunks.
                    # Transport must see only the validated structured_response
                    # emitted from updates, otherwise the policy layer receives
                    # duplicate partial JSON.
                    continue
                if mode == "updates":
                    self._emit_updates(chunk, emit)
        finally:
            usage.add(cap.usage)

    @staticmethod
    def _emit_updates(chunk: Any, emit: Emitter) -> None:
        if not isinstance(chunk, dict):
            return
        for update in chunk.values():
            messages = update.get("messages", []) if isinstance(update, dict) else []
            for message in messages:
                if isinstance(message, ToolMessage):
                    result = (
                        message.artifact
                        if message.artifact is not None
                        else message.content
                    )
                    if message.tool_call_id:
                        emit.tool_result(message.tool_call_id, result)
                elif isinstance(message, AIMessage):
                    for call in message.tool_calls or []:
                        args = call.get("args") or {}
                        emit.tool_call(
                            call.get("id") or "",
                            call.get("name") or "",
                            json.dumps(args),
                            args,
                        )
            structured_response = (
                update.get("structured_response") if isinstance(update, dict) else None
            )
            if isinstance(structured_response, AnswerPlan):
                emit.text_delta(structured_response.model_dump_json())
