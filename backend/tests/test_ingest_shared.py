"""fetch_polite: what it retries, how it reads Retry-After, and what it writes."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest

import ingest._shared as shared
from ingest._shared import (
    HostPacer,
    IngestError,
    Pacer,
    fetch_polite,
    rate_limit_wait,
    receipt_url,
    retry_after_seconds,
)

NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(shared.ingest_settings, "http_initial_backoff_seconds", 0.0)
    monkeypatch.setattr(shared.ingest_settings, "http_max_backoff_seconds", 0.0)
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(shared.asyncio, "sleep", fake_sleep)
    return slept


def frozen_pacer() -> Pacer:
    """No spacing between requests and a clock that never moves, so every recorded
    sleep is exactly the pause the code asked for."""
    return Pacer(min_interval=0.0, clock=lambda: 1000.0)


def run(handler, dest: Path, *, pacer: Pacer | None = None, url: str = "https://api.example.test/prices", **kwargs):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_polite(client, url, dest, pacer=pacer or frozen_pacer(), **kwargs)

    return asyncio.run(go())


class sequence:
    """A handler that plays responses (or raises exceptions) in order and records requests."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.queue = list(responses)
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_writes_body_byte_for_byte_with_hash(tmp_path):
    body = b'{"items": [1, 2, 3], "extra": "kept"}'
    handler = sequence(httpx.Response(200, content=body, headers={"ETag": '"v1"', "X-RateLimit-Remaining": "9"}))

    fetched = run(handler, tmp_path / "page.json")

    assert (tmp_path / "page.json").read_bytes() == body
    assert fetched.sha256 == hashlib.sha256(body).hexdigest()
    assert fetched.size_bytes == len(body)
    assert fetched.headers["etag"] == '"v1"'
    assert fetched.headers["x-ratelimit-remaining"] == "9"
    assert not (tmp_path / "page.json.part").exists()


def test_retries_429_with_retry_after_seconds(tmp_path, no_waiting):
    handler = sequence(
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(200, content=b"{}"),
    )

    run(handler, tmp_path / "p.json")

    assert no_waiting == [7.0]


def test_retries_429_with_retry_after_http_date(tmp_path, no_waiting, monkeypatch):
    monkeypatch.setattr(shared, "now_utc", lambda: NOW)
    when = format_datetime(NOW + timedelta(seconds=30), usegmt=True)
    handler = sequence(
        httpx.Response(429, headers={"Retry-After": when}),
        httpx.Response(200, content=b"{}"),
    )

    run(handler, tmp_path / "p.json")

    assert no_waiting == [30.0]


def test_refuses_a_retry_after_over_the_cap(tmp_path):
    handler = sequence(httpx.Response(429, headers={"Retry-After": "3600"}))

    with pytest.raises(IngestError, match="over the 300s cap"):
        run(handler, tmp_path / "p.json", max_retry_after=300)


def test_retries_5xx_then_succeeds(tmp_path):
    handler = sequence(httpx.Response(503), httpx.Response(502), httpx.Response(200, content=b"ok"))

    run(handler, tmp_path / "p.json")

    assert len(handler.calls) == 3
    assert (tmp_path / "p.json").read_bytes() == b"ok"


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("connection reset"),
        httpx.ReadTimeout("read timed out"),
        httpx.RemoteProtocolError("peer closed connection"),
    ],
)
def test_retries_transport_errors(tmp_path, error):
    handler = sequence(error, httpx.Response(200, content=b"ok"))

    run(handler, tmp_path / "p.json")

    assert (tmp_path / "p.json").read_bytes() == b"ok"


def test_gives_up_after_max_retries(tmp_path):
    handler = sequence(*(httpx.Response(503) for _ in range(3)))

    with pytest.raises(httpx.HTTPStatusError):
        run(handler, tmp_path / "p.json", max_retries=2)
    assert not (tmp_path / "p.json").exists()


def test_other_4xx_raise_at_once_with_the_body(tmp_path):
    handler = sequence(httpx.Response(403, content=b'{"error": "API key not valid"}'))

    with pytest.raises(httpx.HTTPStatusError, match="API key not valid"):
        run(handler, tmp_path / "p.json")
    assert len(handler.calls) == 1


def test_retry_after_parsing():
    assert retry_after_seconds(None, NOW) is None
    assert retry_after_seconds("  ", NOW) is None
    assert retry_after_seconds("120", NOW) == 120.0
    assert retry_after_seconds(format_datetime(NOW - timedelta(seconds=5), usegmt=True), NOW) == 0.0
    with pytest.raises((TypeError, ValueError)):
        retry_after_seconds("soon", NOW)


def test_receipt_url_drops_credentials():
    url = receipt_url("https://api.example.test/skus?pageSize=5000&key=SECRET&pageToken=abc")
    assert "SECRET" not in url
    assert "pageToken=abc" in url


def test_retry_after_zero_still_waits_our_backoff(tmp_path, no_waiting):
    handler = sequence(httpx.Response(503, headers={"Retry-After": "0"}), httpx.Response(200, content=b"{}"))

    run(handler, tmp_path / "p.json", initial_backoff=2.0)

    assert len(no_waiting) == 1 and 2.0 <= no_waiting[0] <= 2.5


def test_a_429_pauses_every_request_to_that_host(tmp_path, no_waiting):
    pacer = frozen_pacer()
    run(sequence(httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, content=b"{}")), tmp_path / "a.json", pacer=pacer)
    no_waiting.clear()

    run(sequence(httpx.Response(200, content=b"{}")), tmp_path / "b.json", pacer=pacer, url="https://api.example.test/other")
    run(sequence(httpx.Response(200, content=b"{}")), tmp_path / "c.json", pacer=pacer, url="https://elsewhere.test/x")

    assert no_waiting == [7.0]  # the same host waits out the pause; another host does not


def test_a_spent_rate_limit_budget_pauses_the_host(tmp_path, no_waiting):
    pacer = frozen_pacer()
    spent = httpx.Response(200, content=b"{}", headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "3"})
    run(sequence(spent), tmp_path / "a.json", pacer=pacer)

    run(sequence(httpx.Response(200, content=b"{}")), tmp_path / "b.json", pacer=pacer)

    assert no_waiting == [3.0]


def test_rate_limit_headers():
    assert rate_limit_wait(httpx.Headers({"X-RateLimit-Remaining": "5", "X-RateLimit-Reset": "1"}), 0) is None
    assert rate_limit_wait(httpx.Headers({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "2"}), 0) == 2.0
    epoch = httpx.Headers({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1792046473"})
    assert rate_limit_wait(epoch, 1792046463.0) == 10.0
    assert rate_limit_wait(httpx.Headers({}), 0) is None


def test_host_pacer_spaces_request_starts(no_waiting):
    pacer = HostPacer(min_interval=0.5, clock=lambda: 100.0)

    async def three():
        for _ in range(3):
            await pacer.wait_turn()

    asyncio.run(three())

    assert no_waiting == [0.5, 1.0]
