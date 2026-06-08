"""Shared guardrail decision types."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

GuardAction = Literal["allow", "block"]
GuardRail = Literal["input", "execution", "retrieval", "output", "eval"]
GuardReason = Literal[
    "safe",
    "prompt_reveal",
    "local_path",
    "fake_tool",
    "out_of_scope",
    "jailbreak",
    "ambiguous",
    "judge_unavailable",
]


class GuardDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: GuardAction
    rail: GuardRail = "input"
    reason: GuardReason
    confidence: float = Field(ge=0.0, le=1.0)
    public_message: str | None = None


class GuardrailUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class GuardrailResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: GuardDecision
    usage: GuardrailUsage = Field(default_factory=GuardrailUsage)
    receipt: dict[str, str | float | bool | int | None]
