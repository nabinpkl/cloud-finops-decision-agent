"""Shared helpers for provider gates: hashing, timestamps, freshness, env, output."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    fetched_at = datetime.fromisoformat(receipt["fetched_at"].replace("Z", "+00:00"))
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


def emit(payload: dict, code: int = 0) -> None:
    print(json.dumps(payload, indent=2))
    sys.exit(code)
