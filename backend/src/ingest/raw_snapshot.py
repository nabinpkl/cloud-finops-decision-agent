"""Raw-first snapshot writing: every provider response stored byte for byte.

A snapshot directory `store/<provider>/<ISO>/` holds one file per request, named
by the provider module (`skus-0001.json`, `eastus-0002.json`, `products.json`).
While a fetch runs, each finished file is appended to `fetch_progress.jsonl`; that
log is the checkpoint a resumed fetch starts from. `receipt.json` is written last
and lists every file with its request URL, status, kept headers and sha256; a
directory without it is an unfinished fetch, never a snapshot. Parsing for prices
happens offline in `normalize/`, so a parser fix is a rebuild, never a refetch.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

import httpx
import orjson

from ingest._shared import (
    PROJECT_ROOT,
    IngestError,
    Pacer,
    RetryCallback,
    emit,
    fetch_polite,
    ingest_settings,
    is_fresh,
    iso_compact,
    iso_z,
    latest_receipt_path,
    log,
    now_utc,
    parse_iso_compact,
    store_root,
)

PROGRESS_FILE = "fetch_progress.jsonl"
RECEIPT_FILE = "receipt.json"
RECEIPT_VERSION = 2
FILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.json$")


@dataclass(frozen=True)
class RawFile:
    name: str
    url: str
    status: int
    headers: dict[str, str]
    sha256: str
    size_bytes: int
    fetched_at: str


@dataclass(frozen=True)
class Receipt:
    receipt_version: int
    success: bool
    provider: str
    service: str
    source_url: str
    store_dir: str
    fetched_at: str
    completed_at: str
    resumed: bool
    files: list[RawFile]
    total_size_bytes: int


class RawSnapshot:
    """One fetch into one snapshot directory, resumable until its receipt is written."""

    def __init__(self, *, provider: str, directory: Path, done: dict[str, RawFile], resumed: bool) -> None:
        self.provider = provider
        self.directory = directory
        self.resumed = resumed
        self._done = done
        # One pacer for the whole run, so every request to a host shares its pace and pauses.
        self.pacer = Pacer()

    @classmethod
    def start(cls, provider: str, *, fresh: bool = False) -> RawSnapshot:
        """Resume the newest unfinished fetch started inside the resume window, or begin
        a new directory. `fresh` skips resuming."""
        root = store_root(provider)
        if not fresh:
            unfinished = _newest_unfinished(root)
            if unfinished is not None:
                started = parse_iso_compact(unfinished.name)
                window = timedelta(hours=ingest_settings.resume_window_hours)
                if now_utc() - started < window:
                    done = _read_progress(unfinished)
                    log(provider, f"resuming {_display(unfinished)} with {len(done)} files already fetched")
                    return cls(provider=provider, directory=unfinished, done=done, resumed=True)
                log(provider, f"leaving unfinished fetch {_display(unfinished)}: started over {window} ago")
        directory = root / iso_compact(now_utc())
        directory.mkdir(parents=True, exist_ok=False)
        (directory / PROGRESS_FILE).touch()
        return cls(provider=provider, directory=directory, done={}, resumed=False)

    @property
    def fetched_at(self) -> datetime:
        return parse_iso_compact(self.directory.name)

    @property
    def file_count(self) -> int:
        return len(self._done)

    def done(self, name: str) -> RawFile | None:
        return self._done.get(name)

    def path(self, name: str) -> Path:
        return self.directory / name

    def read_json(self, name: str) -> Any:
        """Parse a stored response. Fetchers use this only for the next request's input."""
        if name not in self._done:
            raise IngestError(f"{name} has not been fetched into {_display(self.directory)}")
        return orjson.loads(self.path(name).read_bytes())

    async def fetch(
        self,
        client: httpx.AsyncClient,
        name: str,
        url: str,
        *,
        params: dict | None = None,
        timeout: float | None = None,
    ) -> RawFile:
        """Fetch url into `name` unless an earlier run of this snapshot already did."""
        if name in self._done:
            return self._done[name]
        if not FILE_NAME_RE.match(name) or name in (PROGRESS_FILE, RECEIPT_FILE):
            raise IngestError(f"refusing raw file name {name!r}")
        fetched = await fetch_polite(
            client,
            url,
            self.path(name),
            pacer=self.pacer,
            params=params,
            timeout=timeout,
            on_retry=self._on_retry(name),
        )
        record = RawFile(
            name=name,
            url=fetched.url,
            status=fetched.status,
            headers=fetched.headers,
            sha256=fetched.sha256,
            size_bytes=fetched.size_bytes,
            fetched_at=iso_z(now_utc()),
        )
        with self.path(PROGRESS_FILE).open("a") as fh:
            fh.write(json.dumps(asdict(record)) + "\n")
        self._done[name] = record
        return record

    def finish(self, *, service: str, source_url: str) -> Receipt:
        if not self._done:
            raise IngestError(f"{self.provider}: fetch finished without a single file")
        files = [self._done[name] for name in sorted(self._done)]
        receipt = Receipt(
            receipt_version=RECEIPT_VERSION,
            success=True,
            provider=self.provider,
            service=service,
            source_url=source_url,
            store_dir=_display(self.directory),
            fetched_at=iso_z(self.fetched_at),
            completed_at=iso_z(now_utc()),
            resumed=self.resumed,
            files=files,
            total_size_bytes=sum(f.size_bytes for f in files),
        )
        tmp = self.path(RECEIPT_FILE + ".part")
        tmp.write_text(json.dumps(asdict(receipt), indent=2))
        tmp.replace(self.path(RECEIPT_FILE))
        self.path(PROGRESS_FILE).unlink()
        return receipt

    def _on_retry(self, name: str) -> RetryCallback:
        def report(attempt: int, reason: str, wait: float) -> None:
            log(self.provider, f"{name} attempt {attempt}: {reason}; waiting {wait:.1f}s")

        return report


class PageGuard:
    """Stops a paging loop the provider would otherwise keep going: a next token or
    link seen before (a loop) or more pages than INGEST_MAX_PAGES."""

    def __init__(self, label: str, max_pages: int | None = None) -> None:
        self._label = label
        self._max_pages = ingest_settings.max_pages if max_pages is None else max_pages
        self._seen: set[str] = set()

    def follow(self, next_ref: str) -> None:
        """Call before requesting the page `next_ref` points at."""
        if next_ref in self._seen:
            raise IngestError(f"{self._label}: the provider sent the same next page twice ({next_ref[:80]!r})")
        self._seen.add(next_ref)
        if len(self._seen) >= self._max_pages:
            raise IngestError(f"{self._label}: more than {self._max_pages} pages; raise INGEST_MAX_PAGES if real")


def make_client(allowed_hosts: frozenset[str], headers: dict[str, str] | None = None, **kwargs: Any) -> httpx.AsyncClient:
    """A client that sends requests only over https to the provider's own hosts.

    Redirects and provider-supplied next links are followed only while they stay on
    those hosts, so a header credential (GCP's X-goog-api-key, which httpx does not
    strip on a cross-host redirect the way it strips Authorization) never leaves."""

    async def check_host(request: httpx.Request) -> None:
        if request.url.scheme != "https" or request.url.host not in allowed_hosts:
            raise IngestError(f"refusing request to {request.url.scheme}://{request.url.host}: not in {sorted(allowed_hosts)}")

    return httpx.AsyncClient(
        headers=headers or {},
        follow_redirects=True,
        event_hooks={"request": [check_host]},
        **kwargs,
    )


FetchFn = Callable[[RawSnapshot, httpx.AsyncClient], Awaitable[None]]


def run_ingest(
    *,
    provider: str,
    service: str,
    source_url: str,
    fetch: FetchFn,
    allowed_hosts: frozenset[str],
    hint: str,
    description: str,
    client_headers: dict[str, str] | None = None,
    check_ready: Callable[[], object] | None = None,
) -> NoReturn:
    """The CLI every provider module shares: freshness rule, fetch, receipt, JSON out."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--force", action="store_true", help="Bypass the 24h freshness rule.")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Start a new snapshot instead of resuming an unfinished one.",
    )
    args = parser.parse_args()

    if not args.force:
        latest = latest_receipt_path(provider)
        if latest is not None and is_fresh(latest, ingest_settings.snapshot_freshness):
            emit(json.loads(latest.read_text()))

    if check_ready is not None:
        check_ready()

    snapshot = RawSnapshot.start(provider, fresh=args.fresh)

    async def go() -> None:
        async with make_client(allowed_hosts, client_headers) as client:
            await fetch(snapshot, client)

    try:
        asyncio.run(go())
    except (httpx.HTTPError, IngestError) as exc:
        emit(
            {
                "success": False,
                "provider": provider,
                "error": str(exc),
                "hint": hint,
                "store_dir": _display(snapshot.directory),
                "files_fetched": snapshot.file_count,
                "resume": f"run the same command again within {ingest_settings.resume_window_hours:g}h "
                "to continue from the last complete file, or pass --fresh to start over",
            },
            code=1,
        )

    receipt = snapshot.finish(service=service, source_url=source_url)
    emit(asdict(receipt))


def _newest_unfinished(root: Path) -> Path | None:
    if not root.exists():
        return None
    candidates = sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and (path / PROGRESS_FILE).exists() and not (path / RECEIPT_FILE).exists()
    )
    return candidates[-1] if candidates else None


def _read_progress(directory: Path) -> dict[str, RawFile]:
    done: dict[str, RawFile] = {}
    for line in (directory / PROGRESS_FILE).read_text().splitlines():
        if not line.strip():
            continue
        record = RawFile(**json.loads(line))
        if not (directory / record.name).is_file():
            raise IngestError(f"{_display(directory)}: progress lists {record.name} but the file is missing")
        done[record.name] = record
    return done


def _display(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)
