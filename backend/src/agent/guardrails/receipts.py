"""Structured, redacted guardrail receipts."""

from __future__ import annotations

from agent.guardrails.models import GuardDecision, GuardrailResult, GuardrailUsage


def result_from_decision(
    decision: GuardDecision,
    *,
    source: str,
    main_model_skipped: bool,
    usage: GuardrailUsage | None = None,
    error: str | None = None,
) -> GuardrailResult:
    return GuardrailResult(
        decision=decision,
        usage=usage or GuardrailUsage(),
        receipt={
            "rail": decision.rail,
            "action": decision.action,
            "reason": decision.reason,
            "confidence": decision.confidence,
            "source": source,
            "main_model_skipped": main_model_skipped,
            "error": error,
        },
    )
