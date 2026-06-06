"""One server-side assistant turn."""

from __future__ import annotations

from assistant_stream import RunController
from opentelemetry.trace import Status, StatusCode

from api.assistant_transport.emitter import StateEmitter
from api.assistant_transport.policy_emitter import PolicyEmitter
from api.assistant_transport.state import (
    append_assistant_message,
    append_session_limit_message,
    build_turns,
)
from api.budget.identity import session_id_fingerprint
from api.budget.models import SessionUsage
from api.budget.policy import check_session_cap
from api.budget.store import read_session_usage, record_usage
from app_config import settings
from api.observability import get_tracer
from agent.runtime import RunUsage, TurnTokenCapExceeded, get_runtime


async def run_agent_turn(
    controller: RunController,
    *,
    session_id: str,
    hashed_id: str,
) -> None:
    block = check_session_cap(session_id) if settings.budget_enabled else None
    if block is not None:
        append_session_limit_message(controller.state)
        return

    turns = build_turns(controller.state)
    if not turns:
        return

    msg = append_assistant_message(controller.state)
    state_emitter = StateEmitter(controller, msg)
    emitter = PolicyEmitter(state_emitter)

    usage_before = (
        read_session_usage(session_id)
        if settings.budget_enabled
        else SessionUsage(session_id=session_id, input_tokens=0, output_tokens=0)
    )
    run_usage = RunUsage()

    tracer = get_tracer()
    history_text_length = sum(len(turn.content) for turn in turns)
    last_user_length = len(turns[-1].content) if turns else 0
    with tracer.start_as_current_span("agent.turn") as turn_span:
        turn_span.set_attribute("finops.user_message.length", last_user_length)
        turn_span.set_attribute("finops.cross_turn_history.message_count", len(turns))
        turn_span.set_attribute("finops.cross_turn_history.text_length", history_text_length)
        turn_span.set_attribute("finops.session.id_hash", session_id_fingerprint(session_id))
        turn_span.set_attribute("finops.session.tokens_before", usage_before.total)
        turn_span.set_attribute("finops.session.budget_limit", settings.session_token_cap)
        turn_span.set_attribute("finops.agent.runtime", settings.agent_runtime)

        runtime = get_runtime()
        try:
            await runtime.run(turns, emitter, run_usage)
            if not emitter.flush_checked():
                turn_span.set_attribute("finops.policy.final_answer.blocked", True)
                turn_span.set_attribute(
                    "finops.policy.final_answer.violations",
                    "; ".join(emitter.violations),
                )
        except TurnTokenCapExceeded as exc:
            turn_span.record_exception(exc)
            turn_span.set_status(Status(StatusCode.ERROR, str(exc)))
            turn_span.set_attribute("finops.budget.exhausted", True)
            turn_span.set_attribute("finops.budget.scope", "turn")
            emitter.discard_text()
            emitter.text_delta(f"\n\n[turn stopped: {exc}]")
            emitter.flush_unchecked()
        except Exception as exc:
            turn_span.record_exception(exc)
            turn_span.set_status(Status(StatusCode.ERROR, str(exc)))
            emitter.discard_text()
            emitter.text_delta(
                "\n\nThe agent hit an internal error. Try again later."
            )
            emitter.flush_unchecked()
        finally:
            if settings.budget_enabled and (run_usage.input_tokens or run_usage.output_tokens):
                record_usage(
                    session_id=session_id,
                    hashed_id=hashed_id,
                    input_tokens=run_usage.input_tokens,
                    output_tokens=run_usage.output_tokens,
                )
                turn_span.set_attribute(
                    "finops.session.tokens_after",
                    usage_before.total + run_usage.total,
                )
