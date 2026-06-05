"""Configuration for provider ingestion and shared project paths."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_project_root(start: Path) -> Path:
    for candidate in start.parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError(f"could not find project root above {start}")


PROJECT_ROOT = _find_project_root(Path(__file__).resolve())
SRC_ROOT = PROJECT_ROOT / "src"
TAXONOMY_DIR = SRC_ROOT / "normalize" / "taxonomy"


class IngestSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    store_root: str = Field(default="store", validation_alias="INGEST_STORE_ROOT")
    snapshot_freshness_hours: float = Field(
        default=24.0,
        validation_alias="INGEST_SNAPSHOT_FRESHNESS_HOURS",
    )

    http_timeout_seconds: float = Field(
        default=120.0,
        validation_alias="INGEST_HTTP_TIMEOUT_SECONDS",
    )
    http_short_timeout_seconds: float = Field(
        default=60.0,
        validation_alias="INGEST_HTTP_SHORT_TIMEOUT_SECONDS",
    )
    http_large_timeout_seconds: float = Field(
        default=600.0,
        validation_alias="INGEST_HTTP_LARGE_TIMEOUT_SECONDS",
    )
    http_max_retries: int = Field(default=5, validation_alias="INGEST_HTTP_MAX_RETRIES")
    http_initial_backoff_seconds: float = Field(
        default=2.0,
        validation_alias="INGEST_HTTP_INITIAL_BACKOFF_SECONDS",
    )
    http_max_backoff_seconds: float = Field(
        default=60.0,
        validation_alias="INGEST_HTTP_MAX_BACKOFF_SECONDS",
    )

    ibm_pricing_concurrency: int = Field(
        default=8,
        validation_alias="INGEST_IBM_PRICING_CONCURRENCY",
    )

    @property
    def store_root_path(self) -> Path:
        path = Path(self.store_root)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def snapshot_freshness(self) -> timedelta:
        return timedelta(hours=self.snapshot_freshness_hours)


ingest_settings = IngestSettings()
