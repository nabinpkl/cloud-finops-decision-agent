"""Central config for the API process: HTTP surface knobs and the agent's model
provider. Per the no-hardcoded-config rule, every value that changes between
environments lives here and is sourced from the environment / .env, never as a
literal in a source file.

The agent provider (`provider_*`, `model_name`) is deliberately not vendor-fixed:
it points at any OpenAI-compatible base URL (ADR-0009). The provider fields
default empty so importing this module (and running the test suite) never
requires live credentials; the selected runtime validates them at
agent-construction time.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from gates._shared import PROJECT_ROOT


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

    # Agent runtime selection (ADR-0012). Chooses which framework adapter
    # `api.runtime.get_runtime()` returns. "deepagents" (the default) routes to
    # the lean LangChain create_agent adapter; "openai_agents" routes to the
    # OpenAI Agents SDK adapter. Both stacks are core dependencies.
    agent_runtime: Literal["openai_agents", "deepagents"] = "deepagents"

    # Opt-in for the langchain runtime (AGENT_RUNTIME=deepagents): wrap the
    # ChatOpenAI model in the subclass that round-trips `reasoning_content`
    # (ADR-0012). Required for DeepSeek V4 thinking mode via OpenRouter, which
    # returns empty completions when prior-turn reasoning is not echoed back.
    # Off by default; non-thinking models do not need it.
    langchain_reasoning_roundtrip: bool = False

    # Observability (ADR-0010): JSONL OTel traces on disk so any OTel-aware
    # backend can ingest them later without re-instrumenting. All optional.
    otel_enabled: bool = True
    # Resolved relative to PROJECT_ROOT when relative; absolute paths are kept
    # as-is. The exporter creates the parent directory on first export.
    otel_jsonl_path: str = "var/traces/traces.jsonl"
    # Extra exporter that mirrors spans to stderr; useful in dev.
    otel_console_export: bool = False
    # OTel-standard env name (OTEL_GENAI_CAPTURE_MESSAGE_CONTENT). Off by
    # default to keep user prompts and assistant prose out of on-disk traces.
    otel_capture_content: bool = False

    # Budget enforcement (ADR-0011). All limits are tokens; USD is a derived
    # view via api.observability.PRICE_TABLE.
    budget_enabled: bool = True
    budget_db_path: str = "var/budgets.db"
    # Required when budget_enabled=True; the model_validator below enforces
    # presence at process start so we never silently run with a constant key.
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

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        # Env carries a comma-separated string; the in-code default is already a
        # list. Split the former, pass the latter through untouched.
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator(
        "provider_base_url", "provider_api_key", "model_name",
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
    def _require_salt_when_budget_enabled(self) -> Settings:
        # Fail-fast: an empty salt with enforcement on means yesterday's
        # hashed-IPs would be valid forever, undefeating ADR-0011's daily
        # rotation. Better to refuse to boot than to ship a fixed key.
        if self.budget_enabled and not self.budget_ip_hash_salt_secret:
            raise ValueError(
                "BUDGET_IP_HASH_SALT_SECRET must be set when BUDGET_ENABLED=true; "
                "set a 32+ byte random value in .env or disable enforcement."
            )
        return self


settings = Settings()
