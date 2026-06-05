"""IBM ingest stderr logging."""

from __future__ import annotations

import sys


def log(msg: str) -> None:
    print(f"[ibm] {msg}", file=sys.stderr, flush=True)


def on_retry_log(attempt: int, status: int, wait: float) -> None:
    log(f"retry attempt {attempt}: {status}, waiting {wait:.1f}s")
