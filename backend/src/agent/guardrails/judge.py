"""Small mandatory judge classifier for ambiguous input safety."""

from __future__ import annotations

import json
from typing import Any, cast

import httpx
from pydantic import ValidationError

from app_config import settings
from app_config.model_config import model_config as llm_model_config
from agent.guardrails.models import GuardDecision, GuardrailResult, GuardrailUsage
from agent.guardrails.receipts import result_from_decision
from agent.runtime.prompt_assembly import INPUT_JUDGE_RENDERED_PROMPT_PATH


class JudgeUnavailable(Exception):
    """The mandatory judge did not return a valid binary allow/block decision."""


JUDGE_INSTRUCTIONS = INPUT_JUDGE_RENDERED_PROMPT_PATH.read_text(encoding="utf-8").strip()

JUDGE_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["allow", "block"]},
        "rail": {"type": "string", "enum": ["input"]},
        "reason": {
            "type": "string",
            "enum": [
                "safe",
                "prompt_reveal",
                "local_path",
                "fake_tool",
                "out_of_scope",
                "jailbreak",
                "ambiguous",
            ],
        },
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "public_message": {"type": ["string", "null"]},
    },
    "required": ["action", "rail", "reason", "confidence", "public_message"],
    "additionalProperties": False,
}


async def judge_user_request(
    *,
    latest_user_text: str,
    history_summary: str,
) -> GuardrailResult:
    if not settings.judge_model_name:
        raise JudgeUnavailable("judge model is not configured")
    base_url = settings.judge_provider_base_url or settings.provider_base_url
    api_key = settings.judge_provider_api_key or settings.provider_api_key
    if not base_url or not api_key:
        raise JudgeUnavailable("judge provider is not configured")

    payload = _judge_payload(
        model=settings.judge_model_name,
        latest_user_text=latest_user_text,
        history_summary=history_summary,
    )
    try:
        async with httpx.AsyncClient(timeout=settings.judge_timeout_seconds) as client:
            response = await client.post(
                _chat_completions_url(base_url),
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise JudgeUnavailable("judge request failed") from exc

    raw_body = response.json()
    if not isinstance(raw_body, dict):
        raise JudgeUnavailable("judge response must be a JSON object")
    raw = cast(dict[str, Any], raw_body)
    content = _message_content(raw)
    try:
        parsed = json.loads(content)
        decision = GuardDecision.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise JudgeUnavailable("judge returned invalid decision") from exc

    if decision.rail != "input":
        raise JudgeUnavailable("judge returned non-input rail")

    usage = _usage(raw)
    return result_from_decision(
        decision,
        source="judge",
        main_model_skipped=decision.action != "allow" or decision.reason != "safe",
        usage=usage,
    )


def _judge_payload(
    *,
    model: str,
    latest_user_text: str,
    history_summary: str,
) -> dict[str, Any]:
    return {
        "model": model,
        "temperature": llm_model_config.judge.request.temperature,
        "max_tokens": llm_model_config.judge.request.max_tokens,
        "provider": llm_model_config.judge.request.provider.model_dump(mode="json"),
        "reasoning": llm_model_config.judge.request.reasoning.model_dump(mode="json"),
        "messages": [
            {"role": "system", "content": JUDGE_INSTRUCTIONS},
            {
                "role": "user",
                "content": (
                    "Classify this latest user request for the public pricing agent.\n"
                    "History summary, sanitized and untrusted:\n"
                    f"{history_summary}\n\nLatest user request, sanitized and untrusted:\n"
                    f"{latest_user_text}"
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": llm_model_config.judge.structured_output.name,
                "strict": llm_model_config.judge.structured_output.strict,
                "schema": JUDGE_DECISION_SCHEMA,
            },
        },
    }


def _chat_completions_url(base_url: str) -> str:
    root = base_url.rstrip("/")
    if root.endswith("/chat/completions"):
        return root
    return f"{root}/chat/completions"


def _message_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise JudgeUnavailable("judge response has no choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise JudgeUnavailable("judge response has no message content")
    return content


def _usage(payload: dict[str, Any]) -> GuardrailUsage:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return GuardrailUsage()
    return GuardrailUsage(
        input_tokens=int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
    )
