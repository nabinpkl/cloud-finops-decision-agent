"""Shared input contracts for deterministic pricing queries."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from normalize.index import SUPPORTED_PROVIDERS

ProviderName = Literal["aws", "gcp", "azure", "oracle", "vultr", "linode", "ibm"]
FamilyName = Literal["any", "general-purpose", "compute-optimized", "memory-optimized"]
ExpandMode = Literal["cheapest", "full"]

PLAIN_SELECTOR_FORBIDDEN = ("/", "\\", "..", "<", ">")


class PricingInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CompareQueryArgs(PricingInputModel):
    vcpu: int = Field(gt=0, le=1024)
    ram_gb: float = Field(gt=0, le=8192)
    region: str = Field(min_length=1, max_length=64)
    family: FamilyName = "any"
    providers: list[ProviderName] | None = Field(
        default=None,
        min_length=1,
        max_length=len(SUPPORTED_PROVIDERS),
    )
    expand: ExpandMode = "cheapest"

    @field_validator("region", mode="before")
    @classmethod
    def _strip_region(cls, value: object) -> object:
        return _strip_string(value)

    @field_validator("family", "expand", mode="before")
    @classmethod
    def _strip_literal(cls, value: object) -> object:
        return _strip_string(value)

    @field_validator("providers", mode="before")
    @classmethod
    def _strip_providers(cls, value: object) -> object:
        if isinstance(value, list):
            return [_strip_string(item) for item in value]
        return value

    @field_validator("region")
    @classmethod
    def _region_is_plain_selector(cls, value: str) -> str:
        return _plain_selector(value, field_name="region")

    @field_validator("providers")
    @classmethod
    def _providers_are_supported(
        cls,
        value: list[ProviderName] | None,
    ) -> list[ProviderName] | None:
        if value is None:
            return value
        supported = set(SUPPORTED_PROVIDERS)
        unknown = sorted(set(value) - supported)
        if unknown:
            raise ValueError(f"unsupported providers: {', '.join(unknown)}")
        return value


class LookupQueryArgs(PricingInputModel):
    provider: ProviderName
    instance_type: str = Field(min_length=1, max_length=128)
    region: str = Field(min_length=1, max_length=64)

    @field_validator("provider", "instance_type", "region", mode="before")
    @classmethod
    def _strip_string_fields(cls, value: object) -> object:
        return _strip_string(value)

    @field_validator("instance_type", "region")
    @classmethod
    def _plain_query_selector(cls, value: str) -> str:
        return _plain_selector(value, field_name="selector")


def _strip_string(value: object) -> object:
    if isinstance(value, str):
        return value.strip()
    return value


def _plain_selector(value: str, *, field_name: str) -> str:
    if not value or any(part in value for part in PLAIN_SELECTOR_FORBIDDEN):
        raise ValueError(f"{field_name} must be a plain provider or canonical selector")
    return value
