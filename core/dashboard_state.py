"""F1: agent → dashboard IPC via JSON file.

`bash bnbagent` launches the dashboard and the agent as sibling processes.
Each owns its own copy of `core.main.DASHBOARD_STATE`, so the dashboard's
copy stays empty forever and the sidebar/tiles display dashes.

This module bridges the gap: the agent writes a JSON snapshot each tick;
the dashboard reads the file (TTL-cached so it isn't slammed on every
request). The file lives under `~/.bnbagent/` alongside `control.json`,
matching the runtime-IPC convention. Override with
`BNBAGENT_DASHBOARD_STATE_PATH` for tests / ops.

Atomic write semantics: `tempfile.mkstemp` + `os.fsync` + `os.replace`,
mirroring `core/control.py` so a concurrent reader never sees a half-
written file.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_PATH = Path("~/.bnbagent/dashboard_state.json").expanduser()

# How long a cached read is considered fresh. The agent heartbeat ticks
# every ~1s (core/tick.py:138) and the dashboard WS pushes every 1s
# (dashboard/backend/main.py:1008), so a 1s TTL means at most one disk
# read per second per dashboard process — even if /api/* fans into many
# endpoint calls per WS frame.
_CACHE_TTL_S: float = 1.0

# Module-level cache: (deadline_ts, value).
_cache: tuple[float, dict] | None = None


def default_path() -> Path:
    """Return the path the agent writes to and the dashboard reads from.

    Honors `BNBAGENT_DASHBOARD_STATE_PATH` so tests can redirect to a
    `tmp_path` without touching the real `~/.bnbagent/`.
    """
    env = os.environ.get("BNBAGENT_DASHBOARD_STATE_PATH")
    if env:
        return Path(env).expanduser()
    return DEFAULT_PATH


def write_state(state: dict, *, path: Path | None = None) -> None:
    """Atomically serialize `state` to disk.

    Non-JSON-serializable values (Decimal, class instances) fall back to
    `str(v)` rather than raising — the heartbeat must not crash on a
    rogue value. Disk errors are logged and swallowed for the same
    reason; the worst case is the dashboard sees a stale tick.
    """
    p = path or default_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(state, default=str, sort_keys=False).encode()
        fd, tmp = tempfile.mkstemp(prefix=".dashboard_state-", dir=str(p.parent))
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, p)
    except Exception as e:
        log.warning("dashboard_state write failed: %s", e)


def read_state(*, path: Path | None = None) -> dict:
    """Return the latest snapshot, cached for `_CACHE_TTL_S` seconds.

    Returns `{}` when the file is missing or unparseable so the
    dashboard's `_state()` can fall back cleanly.
    """
    global _cache
    now = time.monotonic()
    if _cache is not None and _cache[0] > now:
        return _cache[1]
    p = path or default_path()
    state: dict = {}
    try:
        with p.open("rb") as f:
            state = json.loads(f.read() or b"{}")
            if not isinstance(state, dict):
                state = {}
    except FileNotFoundError:
        state = {}
    except json.JSONDecodeError as e:
        log.debug("dashboard_state malformed: %s", e)
        state = {}
    except Exception as e:
        log.warning("dashboard_state read failed: %s", e)
        state = {}
    _cache = (now + _CACHE_TTL_S, state)
    return state


def _clear_cache_for_tests() -> None:
    """Reset the TTL cache. Test-only seam."""
    global _cache
    _cache = None
