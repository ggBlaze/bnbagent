"""Pytest session fixtures.

Goal: make `git clone && bash install.sh && pytest` (or just `pytest` after
install.sh) succeed without the user having to know that 7 tests need a
real config/policy.yaml at the repo root.

The policy is gitignored on purpose (operator-signed; rotates per wallet).
The shipped example config/policy.yaml.example carries `__SIG__/__EVAL__/__AGENT__`
placeholders. The dev signer (policy.policy_sign --dev) substitutes those
with an ephemeral key (valid 30 days) and writes config/policy.yaml.

This conftest:
  * Before the test session: if config/policy.yaml is missing, run the
    dev signer to produce one. If it already exists (e.g. install.sh
    ran, or the operator already signed their own), leave it alone.
  * After the test session: if we created it, remove it. If the
    operator's signed policy was there, leave it untouched.

This is a belt-and-suspenders: the canonical path is still
`bash install.sh` (which calls the same signer). The conftest only
catches the case where the operator skipped install.sh and went
straight to `pytest`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = REPO_ROOT / "config" / "policy.yaml"
EXAMPLE_PATH = REPO_ROOT / "config" / "policy.yaml.example"


def _have_policy_signer() -> bool:
    try:
        import policy.policy_sign  # noqa: F401
        return True
    except Exception:
        return False


def _generate_dev_policy() -> bool:
    """Run policy_sign --dev and return True on success."""
    if not _have_policy_signer():
        return False
    try:
        r = subprocess.run(
            [sys.executable, "-m", "policy.policy_sign",
             "--config", str(REPO_ROOT / "config" / "config.yaml"),
             "--out",    str(POLICY_PATH),
             "--dev"],
            capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
        )
        return r.returncode == 0 and POLICY_PATH.exists()
    except Exception:
        return False


@pytest.fixture(scope="session", autouse=True)
def _ensure_signed_policy():
    """Session-scoped autouse fixture: make sure config/policy.yaml exists.

    Creates it from the dev signer if missing. Tracks whether we created it
    ourselves so we can clean up at session end without disturbing a
    pre-existing operator-signed policy.
    """
    created_by_us = False
    if not POLICY_PATH.exists():
        if _generate_dev_policy():
            created_by_us = True
    yield  # tests run here
    if created_by_us and POLICY_PATH.exists():
        try:
            POLICY_PATH.unlink()
        except OSError:
            pass


@pytest.fixture(autouse=True)
def _isolate_dashboard_state_ipc(monkeypatch, tmp_path):
    """F1 (v2.1.8): isolate every test from the running agent's IPC file.

    `core/dashboard_state.py` defaults to `~/.bnbagent/dashboard_state.json`
    so the live `bash bnbagent` and the dashboard read the same file. The
    side effect for tests: if the operator is also running the agent
    locally, the dashboard tests pick up its state (component objects
    serialized to str reprs via `default=str`) and fail with things like
    `'str' object has no attribute 'tier'`.

    Pinning the env var per test to a tmp path makes every test see an
    empty file (read_state returns {}) so `_state()` falls back to the
    in-process `DASHBOARD_STATE` dict — which is what the dashboard
    tests mutate directly. Tests that need a SPECIFIC IPC path (e.g.
    test_dashboard_state_ipc.py, test_dashboard_state_file.py) override
    this fixture by setting the env var again in their own fixtures,
    which run AFTER this one.
    """
    monkeypatch.setenv("BNBAGENT_DASHBOARD_STATE_PATH",
                        str(tmp_path / "isolated_dashboard_state.json"))
    # Reset the module-level TTL cache so the previous test's reads
    # (which may have cached an empty dict or a leftover snapshot)
    # don't leak into this test.
    try:
        from core import dashboard_state as _ds
        _ds._clear_cache_for_tests()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _scrub_dotenv_from_test_env(monkeypatch):
    """B (v2.1.8): strip every key defined in `.env` from the test
    process env.

    `core/main.py` and `dashboard/backend/main.py` call `load_dotenv()`
    at import time so `bash bnbagent` picks up the operator's settings
    without needing `set -a; source .env`. The side effect for tests:
    importing those modules (via `from dashboard.backend import main`,
    or transitively through `from core.main import DASHBOARD_STATE`)
    puts the operator's `MINIMAX_API_KEY`, `BNBAGENT_AUTH_MODE`,
    `TWAK_PWD`, etc. into `os.environ` for the rest of the test session.

    Tests that assume those vars are absent then break in non-obvious
    test-ordering ways (test_providers expecting no LLM key, test_auth
    expecting no auth mode, etc.).

    Fix: enumerate the keys defined in the operator's `.env` and
    `monkeypatch.delenv` each one. Tests that explicitly need a value
    set it themselves via monkeypatch, which trumps the delete. If `.env`
    is missing (CI), this is a no-op.
    """
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return
    try:
        from dotenv import dotenv_values
        for key in dotenv_values(env_file):
            monkeypatch.delenv(key, raising=False)
    except Exception:
        # If python-dotenv isn't installed in the test env, silently
        # skip. The agent's load_dotenv would also be a no-op then.
        pass
