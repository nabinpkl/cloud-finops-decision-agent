"""The langchain adapter (ADR-0012): drive `LangChainRuntime.run` with a
scripted fake chat model and assert the neutral `Emitter` receives the right
verbs in order and `RunUsage` sums the per-step `usage_metadata`. No live
provider; the citation/tool body is stubbed so no store/ snapshot is needed."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("langchain")

from langchain_core.language_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402

import agent.runtime.langchain as lc  # noqa: E402
from agent.policy.answer_plan import AnswerPlan  # noqa: E402
from agent.runtime.types import RunUsage, Turn  # noqa: E402


class _FakeToolModel(BaseChatModel):
    """Scripted model: yields queued AIMessages turn by turn; bind_tools is a
    no-op because the tool calls are scripted directly on the messages."""

    responses: list = []
    _cursor: dict = {"n": 0}

    @property
    def _llm_type(self) -> str:
        return "fake-tool"

    def bind_tools(self, tools, **kwargs):  # type: ignore[override]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        i = self._cursor["n"]
        self._cursor["n"] = i + 1
        msg = self.responses[min(i, len(self.responses) - 1)]
        return ChatResult(generations=[ChatGeneration(message=msg)])


class _RecordingEmitter:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def text_delta(self, text: str) -> None:
        self.calls.append(("text", text))

    def tool_call(self, call_id, name, args_text, args) -> None:
        self.calls.append(("tool_call", call_id, name, args))

    def tool_result(self, call_id, result) -> None:
        self.calls.append(("tool_result", call_id, result))


async def _run(monkeypatch) -> tuple[_RecordingEmitter, RunUsage]:
    tool_turn = AIMessage(
        content="",
        tool_calls=[
            {"name": "compare", "args": {"vcpu": 4, "ram_gb": 8, "region": "eu"}, "id": "c1"}
        ],
        usage_metadata={
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "output_token_details": {"reasoning": 3},
        },
    )
    answer_turn = AIMessage(
        content=(
            '{"answer_type":"refusal","price_claims":[],"candidate_claims":[]'
            ',"unmet_requirements":[],"refusal_reason":"out_of_scope"}'
        ),
        usage_metadata={
            "input_tokens": 20,
            "output_tokens": 12,
            "total_tokens": 32,
            "input_token_details": {"cache_read": 4},
        },
    )
    monkeypatch.setattr(
        lc, "_build_model", lambda: _FakeToolModel(responses=[tool_turn, answer_turn])
    )
    # Stub the citation/tool body so the test needs no store/ snapshot. Patch the
    # name the adapter closure actually calls (imported into da's namespace).
    monkeypatch.setattr(
        lc,
        "run_compare_for_model",
        lambda **kw: (
            "<trusted_tool_result><json>{}</json></trusted_tool_result>",
            {"results": [{"provider": "aws", "monthly_usd": 140.16}]},
        ),
    )

    emitter = _RecordingEmitter()
    usage = RunUsage()
    await lc.LangChainRuntime().run([Turn("user", "cheapest 4/8 eu?")], emitter, usage)
    return emitter, usage


def test_emits_tool_call_then_result_and_sums_usage(monkeypatch):
    emitter, usage = asyncio.run(_run(monkeypatch))

    kinds = [c[0] for c in emitter.calls]
    assert "tool_call" in kinds
    assert "tool_result" in kinds
    assert kinds.index("tool_call") < kinds.index("tool_result")

    tool_call = next(c for c in emitter.calls if c[0] == "tool_call")
    assert tool_call[1] == "c1" and tool_call[2] == "compare"
    assert tool_call[3] == {"vcpu": 4, "ram_gb": 8, "region": "eu"}

    tool_result = next(c for c in emitter.calls if c[0] == "tool_result")
    assert tool_result[1] == "c1"
    assert tool_result[2] == {"results": [{"provider": "aws", "monthly_usd": 140.16}]}

    # Usage sums both model steps: 10+20 in, 5+12 out.
    assert usage.input_tokens == 30
    assert usage.output_tokens == 17
    assert usage.total == 47
    assert usage.reasoning_tokens == 3
    assert usage.cached_input_tokens == 4


def test_model_uses_strict_openrouter_routing_with_reasoning(monkeypatch):
    monkeypatch.setattr(lc.settings, "provider_base_url", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(lc.settings, "provider_api_key", "sk-test")
    monkeypatch.setattr(lc.settings, "model_name", "deepseek/deepseek-v4-flash")

    model = lc._build_model()

    assert model.extra_body == {
        "provider": {"require_parameters": True},
        "reasoning": {"effort": "low"},
    }
    assert model.max_tokens == 1200
    assert model.reasoning is None
    assert model.use_responses_api is False
    assert model.disable_streaming is True


def test_structured_response_is_emitted_as_answer_plan_json():
    emitter = _RecordingEmitter()
    plan = AnswerPlan(
        answer_type="refusal",
        price_claims=[],
        candidate_claims=[],
        unmet_requirements=[],
        refusal_reason="out_of_scope",
    )

    lc.LangChainRuntime._emit_updates(
        {"model": {"messages": [], "structured_response": plan}},
        emitter,
    )

    assert emitter.calls == [
        (
            "text",
            (
                '{"answer_type":"refusal","price_claims":[],"candidate_claims":[]'
                ',"unmet_requirements":[],"refusal_reason":"out_of_scope"'
                ',"view_spec":null}'
            ),
        )
    ]
