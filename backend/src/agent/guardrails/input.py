"""Input rail for public assistant turns."""

from __future__ import annotations

import re

from agent.guardrails.judge import JudgeUnavailable, judge_user_request
from agent.guardrails.models import GuardDecision, GuardrailResult
from agent.guardrails.receipts import result_from_decision
from agent.runtime import Turn

INPUT_GUARDRAIL_SAFE_REFUSAL = (
    "I cannot safely process that request in this public pricing agent."
)

_PROMPT_REVEAL_RE = re.compile(
    r"\b(system|developer|hidden|rendered)\s+(prompt|instruction|message)s?\b|"
    r"\bprompt(s)?/(system|rendered)\b|finops-agent\.system\.md|"
    r"\b(show|print|reveal|dump|summarize|translate|encode|diff)\b.*\b(prompt|instruction)s?\b",
    re.I,
)
_LOCAL_PATH_RE = re.compile(
    r"(/Users/|/private/|/etc/|\.env|store_path|raw local path|local file path|"
    r"BUDGET_IP_HASH_SALT_SECRET|PROVIDER_API_KEY)",
    re.I,
)
_FAKE_TOOL_RE = re.compile(
    r"\b(fake|pretend|simulate|forge)\b.*\b(tool|tool result|compare result)\b|"
    r"\bthe tool returned\b|\bsource_result_index\s+\d+\b",
    re.I,
)
_JAILBREAK_RE = re.compile(
    r"\bignore (all )?(previous|prior) instructions\b|"
    r"\bchange roles?\b|\bbecome a shell\b|\bact as\b.*\bwithout restrictions\b",
    re.I,
)


async def run_input_guardrail(turns: list[Turn]) -> GuardrailResult:
    latest_user_text = _latest_user_text(turns)
    deterministic = classify_deterministic(latest_user_text)
    if deterministic.action == "block":
        return result_from_decision(
            deterministic,
            source="deterministic",
            main_model_skipped=True,
        )

    try:
        judge_result = await judge_user_request(
            latest_user_text=latest_user_text,
            history_summary=_history_summary(turns),
        )
    except JudgeUnavailable as exc:
        decision = GuardDecision(
            action="block",
            reason="judge_unavailable",
            confidence=1.0,
            public_message=INPUT_GUARDRAIL_SAFE_REFUSAL,
        )
        return result_from_decision(
            decision,
            source="judge",
            main_model_skipped=True,
            error=type(exc).__name__,
        )

    decision = judge_result.decision
    if decision.action == "allow" and decision.reason == "safe":
        return result_from_decision(
            decision,
            source="judge",
            main_model_skipped=False,
            usage=judge_result.usage,
        )
    return result_from_decision(
        _blocked_judge_decision(decision),
        source="judge",
        main_model_skipped=True,
        usage=judge_result.usage,
    )


def classify_deterministic(text: str) -> GuardDecision:
    if _PROMPT_REVEAL_RE.search(text):
        return GuardDecision(
            action="block",
            reason="prompt_reveal",
            confidence=1.0,
            public_message="I cannot reveal internal instructions, configuration, secrets, or local files.",
        )
    if _LOCAL_PATH_RE.search(text):
        return GuardDecision(
            action="block",
            reason="local_path",
            confidence=1.0,
            public_message="I cannot reveal internal local files or configuration.",
        )
    if _FAKE_TOOL_RE.search(text):
        return GuardDecision(
            action="block",
            reason="fake_tool",
            confidence=1.0,
            public_message=INPUT_GUARDRAIL_SAFE_REFUSAL,
        )
    if _JAILBREAK_RE.search(text):
        return GuardDecision(
            action="block",
            reason="jailbreak",
            confidence=1.0,
            public_message=INPUT_GUARDRAIL_SAFE_REFUSAL,
        )
    return GuardDecision(action="allow", reason="safe", confidence=1.0)


def _blocked_judge_decision(decision: GuardDecision) -> GuardDecision:
    return GuardDecision(
        action="block",
        reason=decision.reason,
        confidence=decision.confidence,
        public_message=decision.public_message or INPUT_GUARDRAIL_SAFE_REFUSAL,
    )


def _latest_user_text(turns: list[Turn]) -> str:
    for turn in reversed(turns):
        if turn.role == "user":
            return turn.content
    return ""


def _history_summary(turns: list[Turn]) -> str:
    parts = [f"{turn.role}: {turn.content}" for turn in turns[:-1]]
    return "\n".join(parts)[-4_000:]
