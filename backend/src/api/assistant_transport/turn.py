"""One server-side assistant turn."""

from __future__ import annotations

from api.assistant_transport.agui import AGUIRunContext, AGUIStateEmitter
from api.assistant_transport.policy_emitter import PolicyEmitter
from api.assistant_transport.state import (
    append_assistant_message,
    append_session_limit_message,
    build_turns,
)
from api.budget.identity import session_id_fingerprint
from api.budget.policy import check_session_cap
from api.budget.store import read_session_usage, record_usage
from app_config import settings
from api.observability import get_tracer
from api.observability.redaction import record_exception, set_error_status
from agent.runtime import RunUsage, TurnTokenCapExceeded, get_runtime
from agent.runtime.prompt_assembly import (
    input_judge_prompt_identity,
    price_agent_prompt_identity,
)
from agent.guardrails.input import run_input_guardrail
from agent.runtime import Turn


async def run_agent_turn(
    controller: AGUIRunContext,
    *,
    session_id: str,
    hashed_id: str,
    view_context: Turn | None = None,
) -> None:
    block = check_session_cap(session_id)
    if block is not None:
        append_session_limit_message(controller.state)
        return

    turns = build_turns(controller.state)
    if not turns:
        return

    usage_before = read_session_usage(session_id)
    run_usage = RunUsage()

    tracer = get_tracer()
    history_text_length = sum(len(turn.content) for turn in turns)
    last_user_length = len(turns[-1].content) if turns else 0
    with tracer.start_as_current_span("agent.turn") as turn_span:
        prompt = price_agent_prompt_identity()
        judge_prompt = input_judge_prompt_identity()
        turn_span.set_attribute("finops.user_message.length", last_user_length)
        turn_span.set_attribute("finops.cross_turn_history.message_count", len(turns))
        turn_span.set_attribute("finops.cross_turn_history.text_length", history_text_length)
        turn_span.set_attribute("finops.session.id_hash", session_id_fingerprint(session_id))
        turn_span.set_attribute("finops.session.tokens_before", usage_before.total)
        turn_span.set_attribute("finops.session.budget_limit", settings.session_token_cap)
        turn_span.set_attribute("finops.agent.runtime", settings.agent_runtime)
        turn_span.set_attribute("finops.prompt.name", prompt.name)
        turn_span.set_attribute("finops.prompt.version", prompt.version)
        turn_span.set_attribute("finops.prompt.rendered_sha256", prompt.rendered_sha256)
        turn_span.set_attribute("finops.judge_prompt.name", judge_prompt.name)
        turn_span.set_attribute("finops.judge_prompt.version", judge_prompt.version)
        turn_span.set_attribute(
            "finops.judge_prompt.rendered_sha256",
            judge_prompt.rendered_sha256,
        )

        try:
            guardrail = await run_input_guardrail(turns)
            run_usage.add_call(
                input_tokens=guardrail.usage.input_tokens,
                output_tokens=guardrail.usage.output_tokens,
                total_tokens=guardrail.usage.total,
                reasoning_tokens=guardrail.usage.reasoning_tokens,
                cached_input_tokens=guardrail.usage.cached_input_tokens,
            )
            turn_span.set_attribute("finops.guardrail.input.action", guardrail.decision.action)
            turn_span.set_attribute("finops.guardrail.input.reason", guardrail.decision.reason)
            turn_span.set_attribute(
                "finops.guardrail.input.confidence",
                guardrail.decision.confidence,
            )
            turn_span.set_attribute(
                "finops.guardrail.input.main_model_skipped",
                bool(guardrail.receipt["main_model_skipped"]),
            )
            if guardrail.decision.action != "allow":
                msg = append_assistant_message(controller.state)
                state_emitter = AGUIStateEmitter(controller, msg)
                state_emitter.text_delta(
                    guardrail.decision.public_message
                    or "I cannot safely process that request in this public pricing agent."
                )
                state_emitter.close_text()
                return

            msg = append_assistant_message(controller.state)
            state_emitter = AGUIStateEmitter(controller, msg)
            emitter = PolicyEmitter(state_emitter)
            runtime = get_runtime()
            # Prepend the (already validated + wrapped) grounding context AFTER
            # the input judge ran on the real conversation, so it is judge-exempt
            # but visible to the model. Ephemeral: not added to controller.state.
            run_turns = [view_context, *turns] if view_context is not None else turns
            await runtime.run(run_turns, emitter, run_usage)
            if not emitter.flush_checked():
                turn_span.set_attribute("finops.policy.final_answer.blocked", True)
                turn_span.set_attribute(
                    "finops.policy.final_answer.violations",
                    "; ".join(emitter.violations),
                )
            state_emitter.close_text()
        except TurnTokenCapExceeded as exc:
            record_exception(turn_span, exc)
            set_error_status(turn_span, exc)
            turn_span.set_attribute("finops.budget.exhausted", True)
            turn_span.set_attribute("finops.budget.scope", "turn")
            emitter.discard_text()
            emitter.text_delta(f"\n\n[turn stopped: {exc}]")
            emitter.flush_unchecked()
            state_emitter.close_text()
        except Exception as exc:
            record_exception(turn_span, exc)
            set_error_status(turn_span, exc)
            emitter.discard_text()
            emitter.text_delta(
                "\n\nThe agent hit an internal error. Try again later."
            )
            emitter.flush_unchecked()
            state_emitter.close_text()
        finally:
            if run_usage.total:
                record_usage(
                    session_id=session_id,
                    hashed_id=hashed_id,
                    usage=run_usage,
                )
                turn_span.set_attribute(
                    "finops.session.tokens_after",
                    usage_before.total + run_usage.total,
                )
                turn_span.set_attribute("finops.usage.input_tokens", run_usage.input_tokens)
                turn_span.set_attribute("finops.usage.output_tokens", run_usage.output_tokens)
                turn_span.set_attribute("finops.usage.total_tokens", run_usage.total)
                turn_span.set_attribute(
                    "finops.usage.reasoning_tokens",
                    run_usage.reasoning_tokens,
                )
                turn_span.set_attribute(
                    "finops.usage.cached_input_tokens",
                    run_usage.cached_input_tokens,
                )
                turn_span.set_attribute("finops.usage.llm_calls", run_usage.llm_calls)
