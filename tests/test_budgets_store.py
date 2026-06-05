"""SQLite store: schema bootstrap and usage persistence."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

import api.budget_store as budget_store
from api.config import settings


def _conn() -> sqlite3.Connection:
    assert budget_store._Init.conn is not None
    return budget_store._Init.conn


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Reset the module latch and point the store at a fresh tmp file."""
    monkeypatch.setattr(settings, "budget_enabled", True)
    monkeypatch.setattr(settings, "budget_db_path", str(tmp_path / "b.db"))
    monkeypatch.setattr(settings, "budget_ip_hash_salt_secret", "test-salt-XX")
    monkeypatch.setattr(budget_store._Init, "done", False)
    monkeypatch.setattr(budget_store._Init, "conn", None)
    yield
    if budget_store._Init.conn is not None:
        budget_store._Init.conn.close()
        budget_store._Init.conn = None
        budget_store._Init.done = False


def test_init_creates_three_tables(fresh_db):
    budget_store.init_budgets()
    cur = _conn().execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    names = {row[0] for row in cur.fetchall()}
    assert {"global_daily", "client_window", "session"}.issubset(names)


def test_init_is_idempotent(fresh_db):
    budget_store.init_budgets()
    conn_first = budget_store._Init.conn
    budget_store.init_budgets()
    assert budget_store._Init.conn is conn_first


def test_read_session_usage_zeros_and_creates_row(fresh_db):
    budget_store.init_budgets()
    usage = budget_store.read_session_usage("sess-A")
    assert usage.input_tokens == 0
    assert usage.output_tokens == 0
    assert usage.total == 0
    # Row now exists.
    row = _conn().execute(
        "SELECT session_id FROM session WHERE session_id=?", ("sess-A",)
    ).fetchone()
    assert row[0] == "sess-A"


def test_record_usage_sums_all_three_tables(fresh_db):
    budget_store.init_budgets()
    budget_store.record_usage("sess-A", "client-X", input_tokens=100, output_tokens=50)
    budget_store.record_usage("sess-A", "client-X", input_tokens=20, output_tokens=30)

    sess = _conn().execute(
        "SELECT tokens_input, tokens_output FROM session WHERE session_id=?", ("sess-A",)
    ).fetchone()
    assert sess == (120, 80)

    daily = _conn().execute(
        "SELECT tokens_input, tokens_output, requests FROM global_daily"
    ).fetchone()
    assert daily == (120, 80, 2)

    client = _conn().execute(
        "SELECT hour_tokens FROM client_window WHERE hashed_id=?", ("client-X",)
    ).fetchone()
    assert client[0] == 200  # input+output across both calls


def test_record_usage_zero_is_noop(fresh_db):
    budget_store.init_budgets()
    budget_store.record_usage("sess-A", "client-X", input_tokens=0, output_tokens=0)
    row = _conn().execute("SELECT count(*) FROM session").fetchone()
    assert row[0] == 0


def test_concurrent_record_usage_serializes_cleanly(fresh_db):
    budget_store.init_budgets()
    iterations = 50

    def worker(n: int) -> None:
        for _ in range(iterations):
            budget_store.record_usage(
                f"sess-{n}", f"client-{n}", input_tokens=1, output_tokens=1
            )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total_in, total_out, total_req = _conn().execute(
        "SELECT tokens_input, tokens_output, requests FROM global_daily"
    ).fetchone()
    expected = 4 * iterations
    assert total_in == expected
    assert total_out == expected
    assert total_req == expected
