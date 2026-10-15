"""Configuration for provider ingestion and shared project paths.

Every number is bounded at load time, so a typo in .env (a zero backoff, a
negative retry count) fails at startup instead of turning into a request burst
or a fetch that sends nothing."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from project_paths import PROJECT_ROOT


class IngestSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    store_root: str = Field(default="store", validation_alias="INGEST_STORE_ROOT")
    snapshot_freshness_hours: float = Field(
        default=24.0,
        gt=0,
        validation_alias="INGEST_SNAPSHOT_FRESHNESS_HOURS",
    )

    # Per-read timeouts: how long one read may wait for the next bytes.
    http_timeout_seconds: float = Field(
        default=120.0,
        gt=0,
        validation_alias="INGEST_HTTP_TIMEOUT_SECONDS",
    )
    http_short_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        validation_alias="INGEST_HTTP_SHORT_TIMEOUT_SECONDS",
    )
    http_large_timeout_seconds: float = Field(
        default=600.0,
        gt=0,
        validation_alias="INGEST_HTTP_LARGE_TIMEOUT_SECONDS",
    )
    # Whole-attempt deadlines: a server trickling bytes cannot hold a request open forever.
    http_attempt_deadline_seconds: float = Field(
        default=600.0,
        gt=0,
        validation_alias="INGEST_HTTP_ATTEMPT_DEADLINE_SECONDS",
    )
    http_large_attempt_deadline_seconds: float = Field(
        default=3600.0,
        gt=0,
        validation_alias="INGEST_HTTP_LARGE_ATTEMPT_DEADLINE_SECONDS",
    )
    http_max_retries: int = Field(default=5, ge=0, le=20, validation_alias="INGEST_HTTP_MAX_RETRIES")
    http_initial_backoff_seconds: float = Field(
        default=2.0,
        gt=0,
        validation_alias="INGEST_HTTP_INITIAL_BACKOFF_SECONDS",
    )
    http_max_backoff_seconds: float = Field(
        default=60.0,
        gt=0,
        validation_alias="INGEST_HTTP_MAX_BACKOFF_SECONDS",
    )
    http_max_retry_after_seconds: float = Field(
        default=300.0,
        gt=0,
        validation_alias="INGEST_HTTP_MAX_RETRY_AFTER_SECONDS",
    )
    http_max_redirects: int = Field(default=3, ge=0, le=10, validation_alias="INGEST_HTTP_MAX_REDIRECTS")
    min_request_interval_seconds: float = Field(
        default=0.2,
        ge=0,
        validation_alias="INGEST_MIN_REQUEST_INTERVAL_SECONDS",
    )
    max_pages: int = Field(default=1000, ge=1, validation_alias="INGEST_MAX_PAGES")
    resume_window_hours: float = Field(
        default=6.0,
        ge=0,
        validation_alias="INGEST_RESUME_WINDOW_HOURS",
    )

    ibm_pricing_concurrency: int = Field(
        default=8,
        ge=1,
        le=32,
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
