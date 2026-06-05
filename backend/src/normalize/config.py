"""Configuration for normalization indexing and quality policy."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from project_paths import PROJECT_ROOT


class NormalizeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    row_drop_hard_fail_pct: float = Field(
        default=50.0,
        validation_alias="NORMALIZE_ROW_DROP_HARD_FAIL_PCT",
    )
    row_drop_warn_pct: float = Field(
        default=30.0,
        validation_alias="NORMALIZE_ROW_DROP_WARN_PCT",
    )
    citation_fail_hard_fail_pct: float = Field(
        default=1.0,
        validation_alias="NORMALIZE_CITATION_FAIL_HARD_FAIL_PCT",
    )
    citation_verifier_sample_cap: int = Field(
        default=50,
        validation_alias="NORMALIZE_CITATION_VERIFIER_SAMPLE_CAP",
    )


normalize_settings = NormalizeSettings()
