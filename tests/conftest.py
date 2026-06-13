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
