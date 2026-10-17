"""Shared helpers for provider ingest: timestamps, freshness, env, output, HTTP."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import sys
import time
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

# Credential values in use this process (header API keys). log() and emit() scrub
# them from every line they print, so an error that quotes a request header (httpx
# does, for an illegal header value) cannot carry a key to the terminal or stdout.
_SECRETS: set[str] = set()


def register_secret(value: str) -> None:
    if value:
        _SECRETS.add(value)


def redact(text: str) -> str:
    for secret in _SECRETS:
        text = text.replace(secret, "[redacted]")
    return text


class IngestError(Exception):
    """A fetch cannot continue: the provider answered in a way we refuse to guess past."""


class HostPacer:
    """Request pacing for one host, shared by every request of a fetch run.

    Requests start at least `min_interval` apart, and none starts while the host is
    paused. Any request that is told to back off (429, 5xx, a spent rate-limit
    budget) pauses the host for all of them, so parallel requests slow down
    together instead of each retrying on its own."""

    def __init__(self, min_interval: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._min_interval = min_interval
        self._clock = clock
        self._next_start = 0.0
        self._paused_until = 0.0
        self._lock = asyncio.Lock()

    async def wait_turn(self) -> None:
        async with self._lock:
            target = max(self._clock(), self._next_start, self._paused_until)
            while True:
                now = self._clock()
                if target > now:
                    await asyncio.sleep(target - now)
                if self._paused_until <= target:
                    break
                # A sibling was told to back off while we slept; honor that pause too.
                target = self._paused_until
            self._next_start = target + self._min_interval

    def pause(self, seconds: float) -> None:
        self._paused_until = max(self._paused_until, self._clock() + seconds)


class Pacer:
    """One HostPacer per host for a fetch run."""

    def __init__(self, min_interval: float | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self._min_interval = (
            ingest_settings.min_request_interval_seconds if min_interval is None else min_interval
        )
        self._clock = clock
        self._hosts: dict[str, HostPacer] = {}

    def host(self, url: httpx.URL | str) -> HostPacer:
        host = httpx.URL(str(url)).host
        if host not in self._hosts:
            self._hosts[host] = HostPacer(self._min_interval, self._clock)
        return self._hosts[host]


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
    print(redact(json.dumps(payload, indent=2)))
    sys.exit(code)


def log(provider: str, msg: str) -> None:
    print(redact(f"[{provider}] {msg}"), file=sys.stderr, flush=True)


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


def rate_limit_wait(headers: httpx.Headers, now_epoch: float) -> float | None:
    """Seconds to hold off when a response says its rate-limit budget is spent.

    Reads `X-RateLimit-Remaining`/`RateLimit-Remaining` and the matching `Reset`,
    which providers send either as seconds to wait (Vultr) or as an epoch time
    (Linode). None while budget remains or when the headers are absent."""
    remaining = _header_number(headers, "ratelimit-remaining")
    if remaining is None or remaining > 0:
        return None
    reset = _header_number(headers, "ratelimit-reset")
    if reset is None:
        return None
    seconds = reset - now_epoch if reset > 1_000_000_000 else reset
    return max(0.0, seconds)


def _header_number(headers: httpx.Headers, suffix: str) -> float | None:
    for name, value in headers.items():
        if name.lower().endswith(suffix):
            try:
                return float(value.strip())
            except ValueError:
                return None
    return None


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
    pacer: Pacer,
    params: dict | None = None,
    json_body: dict | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
    initial_backoff: float | None = None,
    max_backoff: float | None = None,
    max_retry_after: float | None = None,
    deadline: float | None = None,
    on_retry: RetryCallback | None = None,
) -> FetchedFile:
    """GET url (or POST `json_body` to it) and stream the response to dest byte for
    byte, retrying what is worth retrying.

    Every attempt waits its turn on the host's pacer. Retries 429, 5xx and
    transport errors (connection reset, timeout, protocol error). A 429 or 5xx
    pauses the whole host for the wait, honoring Retry-After in either form but
    never waiting less than our own backoff, and refusing a wait longer than
    max_retry_after. A response whose rate-limit headers say the budget is spent
    pauses the host until the reset. A transport error or an attempt that runs past
    `deadline` pauses the host for the backoff and retries. Other non-2xx responses
    raise at once, as does httpx.LocalProtocolError (our own malformed request,
    which retrying cannot fix). The body lands in `<dest>.part` and is renamed onto
    dest only when complete, so dest exists only as a whole response.
    on_retry(attempt, reason, wait) is called before each wait."""
    timeout = ingest_settings.http_timeout_seconds if timeout is None else timeout
    max_retries = ingest_settings.http_max_retries if max_retries is None else max_retries
    delay = ingest_settings.http_initial_backoff_seconds if initial_backoff is None else initial_backoff
    max_backoff = ingest_settings.http_max_backoff_seconds if max_backoff is None else max_backoff
    max_retry_after = (
        ingest_settings.http_max_retry_after_seconds if max_retry_after is None else max_retry_after
    )
    deadline = ingest_settings.http_attempt_deadline_seconds if deadline is None else deadline

    host = pacer.host(url)
    for attempt in range(max_retries + 1):
        backoff = delay + random.uniform(0, delay / 4)
        last = attempt == max_retries
        await host.wait_turn()
        try:
            method = "GET" if json_body is None else "POST"
            async with (
                asyncio.timeout(deadline),
                client.stream(method, url, params=params, json=json_body, timeout=timeout) as resp,
            ):
                status = resp.status_code
                spent = rate_limit_wait(resp.headers, time.time())
                if spent is not None:
                    host.pause(min(spent, max_retry_after))
                if status != 429 and not 500 <= status < 600:
                    if not 200 <= status < 300:
                        await _raise_with_body(resp)
                    return await _stream_to(resp, dest)
                reason = f"HTTP {status}"
                header = resp.headers.get("Retry-After")
                try:
                    told = retry_after_seconds(header, now_utc())
                except (TypeError, ValueError):
                    told = None
                    reason += f", unreadable Retry-After {header!r}"
                wait = max(told, backoff) if told is not None else backoff
                # Pause every request to this host before deciding whether to give up,
                # so siblings slow down even when this request will not retry.
                host.pause(min(wait, max_retry_after))
                if told is not None and told > max_retry_after:
                    raise IngestError(
                        f"{url}: provider asked to wait {told:.0f}s (Retry-After {header!r}), "
                        f"over the {max_retry_after:.0f}s cap"
                    )
                if last:
                    await _raise_with_body(resp)
            if on_retry is not None:
                on_retry(attempt + 1, reason, wait)
        except httpx.LocalProtocolError:
            raise
        except (httpx.TransportError, TimeoutError) as exc:
            host.pause(backoff)
            if last:
                raise
            reason = f"{type(exc).__name__}: {exc}" if str(exc) else f"{type(exc).__name__} after {deadline:.0f}s"
            if on_retry is not None:
                on_retry(attempt + 1, reason, backoff)
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


ERROR_BODY_BYTES = 500


async def _raise_with_body(resp: httpx.Response) -> NoReturn:
    """Raise with the start of the body; never reads more than ERROR_BODY_BYTES of it."""
    head = b""
    async for chunk in resp.aiter_bytes():
        head += chunk
        if len(head) >= ERROR_BODY_BYTES:
            break
    body = head[:ERROR_BODY_BYTES].decode("utf-8", errors="replace")
    raise httpx.HTTPStatusError(
        f"HTTP {resp.status_code} from {receipt_url(resp.request.url)}: {body}",
        request=resp.request,
        response=resp,
    )
