"""Shared helpers for provider ingest: timestamps, freshness, env, output, HTTP."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import NoReturn
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from normalize.snapshot_time import parse_fetched_at

from ingest import config as ingest_config
from project_paths import PROJECT_ROOT, SRC_ROOT as _SRC_ROOT, TAXONOMY_DIR as _TAXONOMY_DIR

SRC_ROOT = _SRC_ROOT
TAXONOMY_DIR = _TAXONOMY_DIR
ingest_settings = ingest_config.ingest_settings

# Response headers worth keeping in the receipt: when the provider says the data
# was served or last changed, and what it said about our rate budget.
KEPT_HEADERS = ("date", "etag", "last-modified", "content-type", "retry-after")
# Query parameters that may carry a credential; never written to a receipt.
SECRET_PARAMS = frozenset({"key", "api_key", "apikey", "access_token"})

RetryCallback = Callable[[int, str, float], object]


class IngestError(Exception):
    """A fetch cannot continue: the provider answered in a way we refuse to guess past."""


@dataclass(frozen=True)
class FetchedFile:
    """One response body written to disk byte for byte, with what the provider said."""

    path: Path
    url: str
    status: int
    headers: dict[str, str]
    sha256: str
    size_bytes: int


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_compact(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H-%M-%SZ")


def parse_iso_compact(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H-%M-%SZ").replace(tzinfo=timezone.utc)


def iso_z(ts: datetime) -> str:
    return ts.isoformat().replace("+00:00", "Z")


def store_root(provider: str) -> Path:
    return ingest_settings.store_root_path / provider


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
        import os

        os.environ.setdefault(key, value)


def emit(payload: dict, code: int = 0) -> NoReturn:
    print(json.dumps(payload, indent=2))
    sys.exit(code)


def log(provider: str, msg: str) -> None:
    print(f"[{provider}] {msg}", file=sys.stderr, flush=True)


def retry_after_seconds(value: str | None, now: datetime) -> float | None:
    """Parse a Retry-After header: delta seconds or an HTTP date (RFC 9110).

    Returns None when absent. A value that is neither form raises ValueError."""
    if value is None or not value.strip():
        return None
    text = value.strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    when = parsedate_to_datetime(text)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - now).total_seconds())


def receipt_url(url: httpx.URL | str) -> str:
    """The request URL as a receipt records it: credentials stripped from the query."""
    parts = urlsplit(str(url))
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in SECRET_PARAMS]
    return urlunsplit(parts._replace(query=urlencode(query)))


async def fetch_polite(
    client: httpx.AsyncClient,
    url: str,
    dest: Path,
    *,
    params: dict | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
    initial_backoff: float | None = None,
    max_backoff: float | None = None,
    max_retry_after: float | None = None,
    on_retry: RetryCallback | None = None,
) -> FetchedFile:
    """GET url and stream the body to dest byte for byte, retrying what is worth retrying.

    Retries 429, 5xx and transport errors (connection reset, timeout, protocol
    error). Honors Retry-After in either form, refusing a wait longer than
    max_retry_after; otherwise backs off exponentially with jitter. Other 4xx
    raise at once. The body lands in `<dest>.part` and is renamed onto dest only
    when complete, so dest exists only as a whole response.
    on_retry(attempt, reason, wait) is called before each wait."""
    timeout = ingest_settings.http_timeout_seconds if timeout is None else timeout
    max_retries = ingest_settings.http_max_retries if max_retries is None else max_retries
    delay = ingest_settings.http_initial_backoff_seconds if initial_backoff is None else initial_backoff
    max_backoff = ingest_settings.http_max_backoff_seconds if max_backoff is None else max_backoff
    max_retry_after = (
        ingest_settings.http_max_retry_after_seconds if max_retry_after is None else max_retry_after
    )

    for attempt in range(max_retries + 1):
        backoff = delay + random.uniform(0, delay / 4)
        try:
            async with client.stream("GET", url, params=params, timeout=timeout) as resp:
                status = resp.status_code
                if status != 429 and not 500 <= status < 600:
                    if status >= 400:
                        await _raise_with_body(resp)
                    return await _stream_to(resp, dest)
                if attempt == max_retries:
                    await _raise_with_body(resp)
                reason = f"HTTP {status}"
                header = resp.headers.get("Retry-After")
                try:
                    told = retry_after_seconds(header, now_utc())
                except (TypeError, ValueError):
                    told = None
                    reason += f", unreadable Retry-After {header!r}"
                if told is not None and told > max_retry_after:
                    raise IngestError(
                        f"{url}: provider asked to wait {told:.0f}s (Retry-After {header!r}), "
                        f"over the {max_retry_after:.0f}s cap"
                    )
                wait = told if told is not None else backoff
        except httpx.TransportError as exc:
            if attempt == max_retries:
                raise
            reason = f"{type(exc).__name__}: {exc}"
            wait = backoff
        if on_retry is not None:
            on_retry(attempt + 1, reason, wait)
        await asyncio.sleep(wait)
        delay = min(delay * 2, max_backoff)
    raise IngestError(f"exceeded {max_retries} retries fetching {url}")


async def _stream_to(resp: httpx.Response, dest: Path) -> FetchedFile:
    part = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    size = 0
    with part.open("wb") as fh:
        async for chunk in resp.aiter_bytes():
            fh.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    part.replace(dest)
    headers = {
        name: value
        for name, value in resp.headers.items()
        if name.lower() in KEPT_HEADERS or "ratelimit" in name.lower()
    }
    return FetchedFile(
        path=dest,
        url=receipt_url(resp.request.url),
        status=resp.status_code,
        headers=headers,
        sha256=digest.hexdigest(),
        size_bytes=size,
    )


async def _raise_with_body(resp: httpx.Response) -> NoReturn:
    body = (await resp.aread())[:500].decode("utf-8", errors="replace")
    raise httpx.HTTPStatusError(
        f"HTTP {resp.status_code} from {receipt_url(resp.request.url)}: {body}",
        request=resp.request,
        response=resp,
    )
