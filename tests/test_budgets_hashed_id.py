"""Salted-hash client id: same-day stability, cross-day rotation, no IP
leaks."""

from __future__ import annotations

import pytest

import api.budget_identity as budget_identity
from api.config import settings


@pytest.fixture
def stable_salt(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "budget_ip_hash_salt_secret", "salt-1234567890")


def test_same_ip_same_day_same_digest(stable_salt, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(budget_identity, "utc_date_str", lambda: "2026-05-31")
    a = budget_identity.hashed_client_id("203.0.113.7")
    b = budget_identity.hashed_client_id("203.0.113.7")
    assert a == b
    assert len(a) == 32


def test_different_ip_different_digest(stable_salt, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(budget_identity, "utc_date_str", lambda: "2026-05-31")
    assert budget_identity.hashed_client_id(
        "203.0.113.7"
    ) != budget_identity.hashed_client_id("203.0.113.8")


def test_salt_rotation_across_days(stable_salt, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(budget_identity, "utc_date_str", lambda: "2026-05-31")
    today = budget_identity.hashed_client_id("203.0.113.7")
    monkeypatch.setattr(budget_identity, "utc_date_str", lambda: "2026-06-01")
    tomorrow = budget_identity.hashed_client_id("203.0.113.7")
    assert today != tomorrow


def test_digest_does_not_contain_raw_ip(stable_salt, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(budget_identity, "utc_date_str", lambda: "2026-05-31")
    ip = "203.0.113.7"
    digest = budget_identity.hashed_client_id(ip)
    # 32 hex chars cannot contain the literal IP, but assert defensively
    # in case someone changes the encoding later.
    assert ip not in digest
    # Each octet substring also absent.
    for piece in ip.split("."):
        assert piece not in digest or piece in "0123456789abcdef" and len(piece) < 3


def test_session_id_fingerprint_is_short_and_stable():
    a = budget_identity.session_id_fingerprint("xyz-session")
    b = budget_identity.session_id_fingerprint("xyz-session")
    assert a == b
    assert len(a) == 8
    assert budget_identity.session_id_fingerprint("other") != a
