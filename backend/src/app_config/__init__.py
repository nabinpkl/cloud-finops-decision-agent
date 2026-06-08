"""Central config for the API process: HTTP surface knobs and the agent's model
provider.

Defaults live in this settings object as reviewable code constants. Values that
actually change between environments are sourced from environment variables or
the repo-root .env file.

The agent provider (`provider_*`, `model_name`) is deliberately not vendor-fixed:
it points at any OpenAI-compatible base URL (ADR-0009). The provider fields
default empty so importing this module (and running the test suite) never
requires live credentials; the selected runtime validates them at
agent-construction time.
"""

from __future__ import annotations

import ipaddress
from typing import Annotated, Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from project_paths import PROJECT_ROOT


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # HTTP surface
    api_port: int = 8000
    cors_allowed_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    # Agent model provider: any OpenAI-compatible endpoint (ADR-0009).
    provider_base_url: str = ""
    provider_api_key: str = ""
    model_name: str = ""
    judge_provider_base_url: str = ""
    judge_provider_api_key: str = ""
    judge_model_name: str = ""
    judge_timeout_seconds: float = 10.0

    # Agent runtime selection (ADR-0012). Chooses which framework adapter
    # `agent.runtime.get_runtime()` returns. "langchain" (the default) routes to
    # the lean LangChain create_agent adapter; "openai_agents" routes to the
    # OpenAI Agents SDK adapter. Both stacks are core dependencies.
    agent_runtime: Literal["openai_agents", "langchain"] = "langchain"

    # Observability (ADR-0010): JSONL OTel traces are always enabled so the
    # agent surface is auditable in every runtime. Export destination and
    # content capture are still deployment knobs.
    # Resolved relative to PROJECT_ROOT when relative; absolute paths are kept
    # as-is. The exporter creates the parent directory on first export.
    otel_jsonl_path: str = "var/traces/traces.jsonl"
    # Extra exporter that mirrors spans to stderr; useful in dev.
    otel_console_export: bool = False
    # OTel-standard env name (OTEL_GENAI_CAPTURE_MESSAGE_CONTENT). Off by
    # default to keep user prompts and assistant prose out of on-disk traces.
    otel_capture_content: bool = False

    # Budget enforcement (ADR-0011). This platform is always treated as a public
    # unauthenticated deployment, so budget enforcement is not a behavior knob.
    # All limits are tokens; USD is a derived view via
    # api.observability.PRICE_TABLE.
    budget_db_path: str = "var/budgets.db"
    # Required at process start so we never silently run with a constant key.
    budget_ip_hash_salt_secret: str = ""
    global_daily_token_cap: int = 10_000_000
    client_rate_requests_per_minute: int = 30
    client_rate_tokens_per_hour: int = 200_000
    session_token_cap: int = 50_000
    session_idle_timeout_minutes: int = 60
    session_cookie_name: str = "finops_session_id"
    session_cookie_secure: bool = False
    turn_token_cap: int = 20_000
    max_turns_per_run: int = 3
    trusted_proxy_count: int = 0
    trusted_proxy_cidrs: Annotated[list[str], NoDecode] = []

    # Deterministic public route limits. These routes do not spend model tokens,
    # but they still read local indexes/snapshot excerpts.
    public_rate_requests_per_minute: int = 120
    excerpt_rate_requests_per_minute: int = 30
    public_max_body_bytes: int = 65_536
    citation_excerpt_max_file_bytes: int = 250_000_000

    # Public assistant transport limits. These fire before runtime/model work
    # so anonymous callers cannot use round-tripped UI state as unbounded prompt
    # or memory input.
    assistant_max_body_bytes: int = 262_144
    assistant_max_commands: int = 8
    assistant_max_state_messages: int = 24
    assistant_max_message_parts: int = 16
    assistant_max_text_chars: int = 8_000
    assistant_max_history_chars: int = 32_000

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        # Env carries a comma-separated string; the in-code default is already a
        # list. Split the former, pass the latter through untouched.
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("trusted_proxy_cidrs", mode="before")
    @classmethod
    def _split_cidrs(cls, value: object) -> object:
        if isinstance(value, str):
            return [cidr.strip() for cidr in value.split(",") if cidr.strip()]
        return value

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def _validate_cidrs(cls, value: list[str]) -> list[str]:
        for cidr in value:
            ipaddress.ip_network(cidr, strict=False)
        return value

    @field_validator(
        "provider_base_url", "provider_api_key", "model_name",
        "judge_provider_base_url", "judge_provider_api_key", "judge_model_name",
        "budget_ip_hash_salt_secret",
        mode="before",
    )
    @classmethod
    def _strip_whitespace(cls, value: object) -> object:
        # Secret managers (Infisical, Doppler, 1Password) often round-trip
        # values with a trailing newline; httpx and HMAC are intolerant. Strip
        # once at the boundary so downstream code stays clean.
        if isinstance(value, str):
            return value.strip()
        return value

    @model_validator(mode="after")
    def _require_budget_salt(self) -> Settings:
        # Fail-fast: an empty salt with enforcement on means yesterday's
        # hashed-IPs would be valid forever, undefeating ADR-0011's daily
        # rotation. Better to refuse to boot than to ship a fixed key.
        if not self.budget_ip_hash_salt_secret:
            raise ValueError(
                "BUDGET_IP_HASH_SALT_SECRET must be set; "
                "set a 32+ byte random value in .env."
            )
        if not self.judge_model_name:
            raise ValueError(
                "JUDGE_MODEL_NAME must be set; "
                "the mandatory input judge cannot be silently bypassed."
            )
        if self.trusted_proxy_count > 0 and not self.trusted_proxy_cidrs:
            raise ValueError(
                "TRUSTED_PROXY_CIDRS must be set when TRUSTED_PROXY_COUNT is non-zero; "
                "otherwise X-Forwarded-For is client-controlled."
            )
        return self


settings = Settings()
