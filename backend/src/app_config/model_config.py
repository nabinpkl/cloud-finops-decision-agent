"""Typed loader for human-readable model request configuration."""

from __future__ import annotations

from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from project_paths import PROJECT_ROOT

MODEL_CONFIG_PATH = PROJECT_ROOT / "config" / "models.yaml"


class ModelConfigBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderRequestConfig(ModelConfigBase):
    require_parameters: bool


class ReasoningRequestConfig(ModelConfigBase):
    effort: Literal["low", "medium", "high"]


class MainExtraBodyConfig(ModelConfigBase):
    provider: ProviderRequestConfig
    reasoning: ReasoningRequestConfig

    def as_request_body(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class MainRequestConfig(ModelConfigBase):
    disable_streaming: bool
    stream_usage: bool
    use_responses_api: bool
    extra_body: MainExtraBodyConfig


class MainStructuredOutputConfig(ModelConfigBase):
    strategy: Literal["provider"]
    schema_name: Literal["AnswerPlan"]
    strict: bool


class MainModelConfig(ModelConfigBase):
    model_env: str
    provider_base_url_env: str
    provider_api_key_env: str
    request: MainRequestConfig
    structured_output: MainStructuredOutputConfig


class JudgeRequestConfig(ModelConfigBase):
    temperature: float | int
    max_tokens: int = Field(gt=0)
    provider: ProviderRequestConfig
    reasoning: ReasoningRequestConfig


class JudgeStructuredOutputConfig(ModelConfigBase):
    type: Literal["json_schema"]
    name: str
    schema_name: Literal["GuardDecision"]
    strict: bool
    actions: list[Literal["allow", "block"]] = Field(min_length=2, max_length=2)


class JudgeModelConfig(ModelConfigBase):
    model_env: str
    provider_base_url_env: str
    provider_api_key_env: str
    fallback_provider_base_url_env: str
    fallback_provider_api_key_env: str
    request: JudgeRequestConfig
    structured_output: JudgeStructuredOutputConfig


class ModelConfig(ModelConfigBase):
    version: Literal[1]
    main: MainModelConfig
    judge: JudgeModelConfig


def load_model_config(path: str | None = None) -> ModelConfig:
    config_path = MODEL_CONFIG_PATH if path is None else PROJECT_ROOT / path
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"model config must be a YAML mapping: {config_path}")
    return ModelConfig.model_validate(raw)


model_config = load_model_config()
