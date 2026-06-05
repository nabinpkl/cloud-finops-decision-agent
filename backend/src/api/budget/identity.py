"""Client and session identity helpers for budget enforcement."""

from __future__ import annotations

import hmac
import secrets
from datetime import datetime, timezone
from hashlib import sha256

from app_config import settings


def utc_date_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def hashed_client_id(ip: str) -> str:
    """Daily-rotating HMAC digest of a client IP."""
    salt = settings.budget_ip_hash_salt_secret.encode("utf-8")
    key = salt + utc_date_str().encode("utf-8")
    return hmac.new(key, ip.encode("utf-8"), sha256).hexdigest()[:32]


def new_session_id() -> str:
    """Opaque random session id for the `finops_session_id` cookie."""
    return secrets.token_urlsafe(24)


def session_id_fingerprint(session_id: str) -> str:
    """Short stable fingerprint for traces, without storing raw session ids."""
    return sha256(session_id.encode("utf-8")).hexdigest()[:8]
