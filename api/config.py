"""Central config for the API process: HTTP surface knobs and the agent's model
provider. Per the no-hardcoded-config rule, every value that changes between
environments lives here and is sourced from the environment / .env, never as a
literal in a source file.

The agent provider (`provider_*`, `model_name`) is deliberately not vendor-fixed:
it points at any OpenAI-compatible base URL (ADR-0009). The provider fields
default empty so importing this module (and running the test suite) never
requires live credentials; `api.agent` validates them at agent-construction time.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import field_validator
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

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        # Env carries a comma-separated string; the in-code default is already a
        # list. Split the former, pass the latter through untouched.
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


settings = Settings()
