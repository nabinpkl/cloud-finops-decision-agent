"""Shared test fixtures and helpers.

Two lanes (per the discussion in this session):
  - mocked integration: deterministic fixtures, no real store/ dependency.
    These build in-memory parquet frames or tiny on-disk stores and patch the
    loader / data_quality seams.
  - e2e real-file: run against whatever is in store/ (marked `e2e`, skipped
    when nothing is indexed).
"""

from __future__ import annotations

import os

# Budget enforcement (ADR-0011) is off by default in tests; tests that need
# it on flip settings.budget_enabled at the instance level via monkeypatch.
# Setting the salt belt-and-braces in case a test enables budgets without
# providing one explicitly.
os.environ.setdefault("BUDGET_ENABLED", "false")
os.environ.setdefault(
    "BUDGET_IP_HASH_SALT_SECRET", "test-salt-not-a-real-secret-32-bytes"
)
