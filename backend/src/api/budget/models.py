"""Budget enforcement data models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class BudgetModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SessionUsage(BudgetModel):
    session_id: str
    input_tokens: int
    output_tokens: int
    total_tokens: int = 0
    reasoning_tokens: int = 0
    cached_input_tokens: int = 0

    @property
    def total(self) -> int:
        return self.total_tokens or (self.input_tokens + self.output_tokens)


class BudgetBlock(BudgetModel):
    """A cap-hit decision surfaced by middleware or transport."""

    reason: Literal[
        "global_daily",
        "client_request_rate",
        "client_token_rate",
        "public_route_request_rate",
        "session",
    ]
    http_status: int
    retry_after_seconds: int
