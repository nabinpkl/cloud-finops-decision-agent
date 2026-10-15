"""RawSnapshot: files land byte for byte, progress survives a failure, the receipt
comes last and lists every file."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

import ingest._shared as shared
import ingest.raw_snapshot as raw_snapshot
from ingest._shared import IngestError
from ingest.raw_snapshot import PROGRESS_FILE, RECEIPT_FILE, PageGuard, RawSnapshot, make_client


@pytest.fixture
def ticking_clock(monkeypatch):
    """Each call is one second later, so two snapshots never share a directory name."""
    start = datetime(2026, 10, 15, 8, 0, 0, tzinfo=timezone.utc)
    ticks = iter(range(10_000))
    monkeypatch.setattr(raw_snapshot, "now_utc", lambda: start + timedelta(seconds=next(ticks)))


@pytest.fixture
def store(monkeypatch, tmp_path):
    root = tmp_path / "store"
    monkeypatch.setattr(shared.ingest_settings, "store_root", str(root))
    monkeypatch.setattr(shared.ingest_settings, "http_initial_backoff_seconds", 0.0)
    monkeypatch.setattr(shared.ingest_settings, "http_max_retries", 0)
    monkeypatch.setattr(shared.ingest_settings, "min_request_interval_seconds", 0.0)
    return root


def fetch_all(snapshot: RawSnapshot, handler, names_urls: list[tuple[str, str]]):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            for name, url in names_urls:
                await snapshot.fetch(client, name, url)

    asyncio.run(go())


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=f'{{"path": "{request.url.path}"}}'.encode())


def test_finish_writes_receipt_and_drops_progress(store):
    snapshot = RawSnapshot.start("vultr")
    fetch_all(snapshot, ok, [("plans-0002.json", "https://x.test/b"), ("plans-0001.json", "https://x.test/a")])

    receipt = snapshot.finish(service="plans", source_url="https://x.test")

    on_disk = json.loads((snapshot.directory / RECEIPT_FILE).read_text())
    assert on_disk["receipt_version"] == 2
    assert [f["name"] for f in on_disk["files"]] == ["plans-0001.json", "plans-0002.json"]
    assert on_disk["files"][0]["url"] == "https://x.test/a"
    assert receipt.total_size_bytes == sum(f["size_bytes"] for f in on_disk["files"])
    assert not (snapshot.directory / PROGRESS_FILE).exists()


def test_resume_skips_files_already_fetched(store):
    first = RawSnapshot.start("azure")

    def fail_second(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/b":
            raise httpx.ConnectError("reset")
        return ok(request)

    with pytest.raises(httpx.ConnectError):
        fetch_all(first, fail_second, [("eastus-0001.json", "https://x.test/a"), ("eastus-0002.json", "https://x.test/b")])
    assert not (first.directory / RECEIPT_FILE).exists()

    seen: list[str] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return ok(request)

    second = RawSnapshot.start("azure")
    fetch_all(second, record, [("eastus-0001.json", "https://x.test/a"), ("eastus-0002.json", "https://x.test/b")])

    assert second.directory == first.directory
    assert second.resumed
    assert seen == ["/b"]
    assert second.finish(service="vm", source_url="https://x.test").resumed is True


def test_fresh_ignores_an_unfinished_fetch(store, ticking_clock):
    first = RawSnapshot.start("gcp")
    fetch_all(first, ok, [("skus-0001.json", "https://x.test/a")])

    second = RawSnapshot.start("gcp", fresh=True)

    assert second.directory != first.directory
    assert second.done("skus-0001.json") is None


def test_unfinished_fetch_outside_the_window_is_left_alone(store, monkeypatch, ticking_clock):
    first = RawSnapshot.start("gcp")
    monkeypatch.setattr(shared.ingest_settings, "resume_window_hours", 0.0)

    second = RawSnapshot.start("gcp")

    assert second.directory != first.directory
    assert first.directory.exists()


def test_refuses_names_that_could_escape_or_clobber(store):
    snapshot = RawSnapshot.start("ibm")
    for name in ("../x.json", "sub/x.json", RECEIPT_FILE, PROGRESS_FILE, "x.txt"):
        with pytest.raises(IngestError):
            fetch_all(snapshot, ok, [(name, "https://x.test/a")])


def test_finish_without_files_fails_loud(store):
    with pytest.raises(IngestError):
        RawSnapshot.start("oracle").finish(service="x", source_url="https://x.test")


def test_page_guard_stops_a_repeated_next_page():
    guard = PageGuard("test")
    guard.follow("token-2")
    with pytest.raises(IngestError, match="same next page twice"):
        guard.follow("token-2")


def test_page_guard_stops_at_the_page_cap():
    guard = PageGuard("test", max_pages=3)
    guard.follow("2")
    guard.follow("3")
    with pytest.raises(IngestError, match="more than 3 pages"):
        guard.follow("4")


def get_via(client_hosts: frozenset[str], handler, url: str):
    async def go():
        async with make_client(client_hosts, {"X-goog-api-key": "k"}, transport=httpx.MockTransport(handler)) as client:
            return await client.get(url)

    return asyncio.run(go())


def test_client_refuses_other_hosts_and_plain_http():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200)

    for url in ("https://evil.test/x", "http://api.example.test/x"):
        with pytest.raises(IngestError, match="refusing request"):
            get_via(frozenset({"api.example.test"}), handler, url)
    assert seen == []


def test_client_follows_a_redirect_only_on_the_same_host():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/moved-here":
            return httpx.Response(302, headers={"Location": "https://api.example.test/new"})
        if request.url.path == "/moved-away":
            return httpx.Response(302, headers={"Location": "https://evil.test/steal"})
        return httpx.Response(200)

    hosts = frozenset({"api.example.test"})
    assert get_via(hosts, handler, "https://api.example.test/moved-here").status_code == 200
    with pytest.raises(IngestError, match="evil.test"):
        get_via(hosts, handler, "https://api.example.test/moved-away")
    assert not any("evil.test" in url for url in seen)
