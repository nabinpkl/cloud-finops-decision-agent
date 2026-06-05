"""Model-provider name inference for telemetry attributes."""

from __future__ import annotations

from urllib.parse import urlparse


def infer_provider_name(base_url: str) -> str:
    if not base_url:
        return "unknown"
    host = (urlparse(base_url).hostname or "").lower()
    if "openai.com" in host:
        return "openai"
    if "openrouter" in host:
        return "openrouter"
    if "anthropic" in host:
        return "anthropic"
    if "localhost" in host or "127.0.0.1" in host:
        return "local"
    if "googleapis" in host or "google" in host:
        return "google"
    return host or "unknown"

