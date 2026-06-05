"""Shared helpers for provider gates: hashing, timestamps, freshness, env, output, HTTP."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NoReturn

import httpx

from normalize.snapshot_time import parse_fetched_at

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class FileRecord:
    name: str
    hash: str
    size_bytes: int
    source_url: str


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_compact(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H-%M-%SZ")


def iso_z(ts: datetime) -> str:
    return ts.isoformat().replace("+00:00", "Z")


def store_root(provider: str) -> Path:
    return PROJECT_ROOT / "store" / provider


def latest_receipt_path(provider: str) -> Path | None:
    root = store_root(provider)
    if not root.exists():
        return None
    receipts = sorted(root.glob("*/receipt.json"))
    return receipts[-1] if receipts else None


def is_fresh(receipt_path: Path, freshness: timedelta) -> bool:
    receipt = json.loads(receipt_path.read_text())
    fetched_at = parse_fetched_at(receipt["fetched_at"])
    return now_utc() - fetched_at < freshness


def load_dotenv_if_present() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def emit(payload: dict, code: int = 0) -> NoReturn:
    print(json.dumps(payload, indent=2))
    sys.exit(code)


DEFAULT_MAX_RETRIES = 5
DEFAULT_INITIAL_BACKOFF = 2.0
DEFAULT_MAX_BACKOFF = 60.0


async def fetch_polite(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict | None = None,
    timeout: float = 120.0,
    max_retries: int = DEFAULT_MAX_RETRIES,
    initial_backoff: float = DEFAULT_INITIAL_BACKOFF,
    max_backoff: float = DEFAULT_MAX_BACKOFF,
    on_retry: Callable[[int, int, float], object] | None = None,
) -> httpx.Response:
    # Honor Retry-After when present, exponential backoff otherwise. Retries on 429
    # and 5xx; 4xx other than 429 raise immediately. on_retry(attempt, status, wait)
    # is optional; gates that want stderr breadcrumbs supply it.
    delay = initial_backoff
    for attempt in range(max_retries + 1):
        resp = await client.get(url, params=params, timeout=timeout)
        if resp.status_code == 429 or 500 <= resp.status_code < 600:
            if attempt == max_retries:
                resp.raise_for_status()
            retry_after = resp.headers.get("Retry-After")
            wait = float(retry_after) if retry_after else delay
            if on_retry is not None:
                on_retry(attempt + 1, resp.status_code, wait)
            await asyncio.sleep(wait)
            delay = min(delay * 2, max_backoff)
            continue
        resp.raise_for_status()
        return resp
    raise httpx.HTTPError(f"exceeded {max_retries} retries fetching {url}")
