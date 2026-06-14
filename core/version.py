"""BNB Agent — single source of truth for the version string.

Keeps the dashboard / API / pyproject.toml in sync. The `__version__`
constant here is the canonical value; `pyproject.toml` is bumped
alongside every release so `pip install -e .` and `python -c "import
bnbagent"` agree.

The `git_commit` is read lazily from the `.git` directory on import
(falls back to "unknown" in source tarballs or in containers without
.git). It's used by the dashboard footer + the `/api/version`
endpoint so judges can verify exactly which build they're looking at.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

# Release version. Bump alongside pyproject.toml.
__version__ = "2.1.6"


def _read_git_commit() -> str:
    """Return the current short commit hash, or 'unknown' if not a git checkout."""
    try:
        repo = Path(__file__).resolve().parents[1]
        if not (repo / ".git").exists():
            return "unknown"
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo),
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        return out.decode("utf-8", errors="replace").strip() or "unknown"
    except Exception:
        return "unknown"


# Resolved once at import time. Cheap (one subprocess call, ≤2s timeout).
git_commit: str = _read_git_commit()


def version_info() -> dict:
    """Return the canonical version bundle for the /api/version endpoint."""
    return {
        "version": __version__,
        "git_commit": git_commit,
    }
