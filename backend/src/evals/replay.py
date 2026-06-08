"""Offline replay lane for the agent event contract.

This is intentionally model-free: it verifies that eval cases can drive the
neutral runtime/emitter shape (`tool_call`, `tool_result`, `text_delta`) and
then grades the transcript reconstructed from those emitted events. Live model
quality belongs in a separate, credentialed lane.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

from agent.runtime.types import Emitter, RunUsage, Turn
from evals.cases import EvalCase
from evals.graders import CheckResult, grade_case


@dataclass
class ReplayEmitter(Emitter):
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)

    def text_delta(self, text: str) -> None:
        self.text += text

    def tool_call(self, call_id: str, name: str, args_text: str, args: dict) -> None:
        self.tool_calls.append(
            {"id": call_id, "name": name, "args_text": args_text, "args": args}
        )

    def tool_result(self, call_id: str, result: object) -> None:
        self.tool_results.append({"id": call_id, "result": result})


class ReplayRuntime:
    """Fake runtime that replays one YAML case through the real emitter verbs."""

    def __init__(self, case: EvalCase) -> None:
        self._case = case

    async def run(self, turns: list[Turn], emit: Emitter, usage: RunUsage) -> None:
        if not turns or turns[-1].role != "user":
            raise ValueError("replay expects at least one user turn")
        call = self._case.tool_call
        if call:
            raw_args = call.get("args")
            args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
            call_id = f"eval-{self._case.id}"
            emit.tool_call(
                call_id=call_id,
                name=str(call.get("name", "")),
                args_text=json.dumps(args),
                args=args,
            )
            emit.tool_result(call_id, self._case.tool_result)
        text = (
            json.dumps(self._case.answer_plan)
            if self._case.answer_plan is not None
            else self._case.final_answer
        )
        emit.text_delta(text)
        usage.input_tokens = sum(len(turn.content.split()) for turn in turns)
        usage.output_tokens = len(text.split())


@dataclass(frozen=True)
class ReplayResult:
    case: EvalCase
    usage: RunUsage
    checks: list[CheckResult]
    elapsed_ms: float
    tool_call_count: int


def replay_case(case: EvalCase) -> ReplayResult:
    emitter = ReplayEmitter()
    usage = RunUsage()
    runtime = ReplayRuntime(case)
    turns = [Turn(role=turn.role, content=turn.content) for turn in case.turns]
    started = time.perf_counter()
    asyncio.run(runtime.run(turns, emitter, usage))
    elapsed_ms = (time.perf_counter() - started) * 1000
    replayed = _case_from_emitted(case, emitter)
    tool_call_count = len(emitter.tool_calls)
    checks = [*grade_case(replayed), *_grade_gates(case, usage, elapsed_ms, tool_call_count)]
    return ReplayResult(
        case=replayed,
        usage=usage,
        checks=checks,
        elapsed_ms=elapsed_ms,
        tool_call_count=tool_call_count,
    )


def _case_from_emitted(case: EvalCase, emitter: ReplayEmitter) -> EvalCase:
    if not case.tool_call:
        if emitter.tool_calls or emitter.tool_results:
            raise ValueError(f"{case.id}: replay emitted unexpected tool events")
        updates: dict[str, Any] = {}
        if case.answer_plan is not None:
            updates["answer_plan"] = json.loads(emitter.text)
            updates["final_answer"] = case.final_answer
        else:
            updates["final_answer"] = emitter.text
        return case.model_copy(update=updates)

    if len(emitter.tool_calls) != 1:
        raise ValueError(f"{case.id}: replay emitted {len(emitter.tool_calls)} tool calls")
    if len(emitter.tool_results) != 1:
        raise ValueError(f"{case.id}: replay emitted {len(emitter.tool_results)} tool results")
    call = emitter.tool_calls[0]
    result = emitter.tool_results[0]
    if call["id"] != result["id"]:
        raise ValueError(f"{case.id}: replay tool result id does not match call id")
    updates: dict[str, Any] = {
        "tool_call": {"name": call["name"], "args": call["args"]},
        "tool_result": result["result"],
    }
    if case.answer_plan is not None:
        updates["answer_plan"] = json.loads(emitter.text)
        updates["final_answer"] = case.final_answer
    else:
        updates["final_answer"] = emitter.text
    return case.model_copy(update=updates)


def _grade_gates(
    case: EvalCase,
    usage: RunUsage,
    elapsed_ms: float,
    tool_call_count: int,
) -> list[CheckResult]:
    gates = case.gates
    if gates is None:
        return []
    results: list[CheckResult] = []
    if gates.max_latency_ms is not None:
        results.append(
            _gate_result(
                "gate_latency",
                elapsed_ms <= gates.max_latency_ms,
                f"latency {elapsed_ms:.2f}ms <= {gates.max_latency_ms:.2f}ms",
            )
        )
    if gates.max_tool_calls is not None:
        results.append(
            _gate_result(
                "gate_tool_calls",
                tool_call_count <= gates.max_tool_calls,
                f"tool calls {tool_call_count} <= {gates.max_tool_calls}",
            )
        )
    if gates.max_input_tokens is not None:
        results.append(
            _gate_result(
                "gate_input_tokens",
                usage.input_tokens <= gates.max_input_tokens,
                f"input tokens {usage.input_tokens} <= {gates.max_input_tokens}",
            )
        )
    if gates.max_output_tokens is not None:
        results.append(
            _gate_result(
                "gate_output_tokens",
                usage.output_tokens <= gates.max_output_tokens,
                f"output tokens {usage.output_tokens} <= {gates.max_output_tokens}",
            )
        )
    return results


def _gate_result(name: str, passed: bool, detail: str) -> CheckResult:
    return CheckResult(
        name=name,
        passed=passed,
        detail=detail,
        failure_label=None if passed else "operational_gate",
    )
