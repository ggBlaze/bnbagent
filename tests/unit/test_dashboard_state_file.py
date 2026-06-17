"""F1: agent ↔ dashboard IPC via JSON file.

`bash bnbagent` launches the dashboard and the agent as siblings:

    bash bnbagent
      ├─ python -m dashboard.backend.main    # FastAPI on :8000
      └─ python -m core.main                 # trading agent loop

`core.main` defines a module-level `DASHBOARD_STATE` dict that the agent
mutates each tick; `dashboard/backend/main.py` imports it. Because the
two processes have separate memory, the dashboard's copy is always the
initial empty dict. Sidebar/tiles all show dashes.

Fix: the agent writes a JSON snapshot to a file each tick; the dashboard
reads the file (TTL-cached so it isn't slammed on every request). This
is `core/dashboard_state.py` — these are its tests.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from core import dashboard_state as ds


@pytest.fixture(autouse=True)
def _clean_cache_and_env(monkeypatch, tmp_path):
    """Reset module-level cache and point the default path at tmp_path
    so tests don't touch the real ~/.bnbagent/."""
    ds._clear_cache_for_tests()
    monkeypatch.setenv("BNBAGENT_DASHBOARD_STATE_PATH", str(tmp_path / "ds.json"))
    yield
    ds._clear_cache_for_tests()


def test_default_path_respects_env(monkeypatch, tmp_path):
    target = tmp_path / "custom" / "dashboard_state.json"
    monkeypatch.setenv("BNBAGENT_DASHBOARD_STATE_PATH", str(target))
    assert ds.default_path() == target


def test_default_path_falls_back_to_bnbagent_home(monkeypatch):
    monkeypatch.delenv("BNBAGENT_DASHBOARD_STATE_PATH", raising=False)
    p = ds.default_path()
    assert p.name == "dashboard_state.json"
    # Lives under the runtime IPC dir (~/.bnbagent/), same pattern as
    # control.json. Don't pin the exact home — different CIs have
    # different HOMEs — but pin the .bnbagent/ segment.
    assert ".bnbagent" in str(p), f"expected default under .bnbagent/, got {p}"


def test_write_then_read_roundtrips_simple_dict(tmp_path):
    state = {"stats": {"equity": "100.50", "pnl_today": "1.25"},
             "config": {"mode": "mainnet", "chain_id": 56},
             "updated_at": 1718600000}
    ds.write_state(state)
    out = ds.read_state()
    assert out == state


def test_write_creates_parent_directory(monkeypatch, tmp_path):
    target = tmp_path / "deep" / "nested" / "dashboard_state.json"
    monkeypatch.setenv("BNBAGENT_DASHBOARD_STATE_PATH", str(target))
    ds._clear_cache_for_tests()
    ds.write_state({"a": 1})
    assert target.exists()
    assert json.loads(target.read_text()) == {"a": 1}


def test_read_returns_empty_dict_when_file_missing():
    # Nothing was written; file does not exist
    assert ds.read_state() == {}


def test_read_returns_empty_dict_when_file_corrupt(tmp_path):
    path = ds.default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not: valid json")  # malformed
    assert ds.read_state() == {}


def test_non_serializable_values_become_strings():
    """Components are class instances; the agent's dashboard_state may
    contain them. write_state must not crash; it falls back to str(v)
    so the disk file is always valid JSON."""
    from decimal import Decimal
    class _Component:
        def __repr__(self) -> str:
            return "<BSCClient testnet>"
    state = {
        "stats": {"equity": Decimal("100.50")},
        "components": {"identity": {"token_id": 42},  # already a dict
                        "bsc": _Component()},        # instance
    }
    ds.write_state(state)
    out = ds.read_state()
    # Identity stayed a dict.
    assert out["components"]["identity"] == {"token_id": 42}
    # Decimal serialized to string via default=str.
    assert out["stats"]["equity"] == "100.50"
    # Instance serialized to its repr.
    assert "BSCClient" in out["components"]["bsc"]


def test_ttl_cache_avoids_repeated_disk_reads(monkeypatch):
    """Each request hitting /api/* should not hammer the disk. Within
    the TTL window, multiple read_state() calls open the file once."""
    ds.write_state({"x": 1})
    opens = {"n": 0}
    real_open = Path.open
    def counting_open(self, *a, **kw):
        if self == ds.default_path():
            opens["n"] += 1
        return real_open(self, *a, **kw)
    monkeypatch.setattr(Path, "open", counting_open)
    ds._clear_cache_for_tests()
    for _ in range(5):
        ds.read_state()
    assert opens["n"] == 1, (
        f"expected 1 disk open within TTL window; got {opens['n']}"
    )


def test_ttl_cache_refreshes_after_expiry(monkeypatch):
    """After the TTL elapses, the next read picks up new content."""
    ds.write_state({"v": "first"})
    monkeypatch.setattr(ds, "_CACHE_TTL_S", 0.05)
    ds._clear_cache_for_tests()
    first = ds.read_state()
    assert first == {"v": "first"}
    ds.write_state({"v": "second"})
    # Force cache to consider its entry stale.
    time.sleep(0.07)
    second = ds.read_state()
    assert second == {"v": "second"}


def test_write_is_atomic_no_partial_reads(tmp_path):
    """write_state must be atomic: a concurrent reader either sees the
    old content or the new content, never a half-written file. Mirror
    the tempfile + os.replace pattern used by core/control.py."""
    ds.write_state({"v": 0})
    stop = threading.Event()
    seen_invalid: list[str] = []

    def reader():
        while not stop.is_set():
            try:
                # Bypass the TTL cache — we want to hit disk every time.
                ds._clear_cache_for_tests()
                out = ds.read_state()
            except Exception as e:
                seen_invalid.append(repr(e))
                continue
            # The only legal states are the empty fallback (file briefly
            # absent during rename) or {"v": int}. A half-written read
            # would raise JSONDecodeError which is swallowed → {}.
            if out and "v" not in out:
                seen_invalid.append(f"unexpected state {out!r}")

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        for i in range(100):
            ds.write_state({"v": i, "padding": "x" * 1000})
    finally:
        stop.set()
        t.join(timeout=2)
    assert not seen_invalid, f"reader saw inconsistent states: {seen_invalid[:5]}"


def test_write_swallows_disk_errors(monkeypatch, tmp_path):
    """Disk full / permission denied on the heartbeat file must not
    crash the agent. write_state best-efforts and logs."""
    def boom(self, *a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(Path, "replace", boom)
    # Should NOT raise.
    ds.write_state({"x": 1})
