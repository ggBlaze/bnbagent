"""A (v2.1.8): control IPC primitives for the restart-agent flow.

The dashboard's POST /api/agent/restart writes a marker into
~/.bnbagent/control.json; the agent's heartbeat polls control once per
tick (already does — for kill_switch, sleeve toggles, etc.) and triggers
a graceful shutdown with exit code 75 when the marker is present. The
bash wrapper loops on exit 75 to re-exec the agent process.

These tests pin the helpers around the marker (write, read, clear) so
the higher layers (heartbeat handler, dashboard endpoint, bash loop) all
agree on the wire format.
"""
from __future__ import annotations

import json

import pytest

from core import control


@pytest.fixture(autouse=True)
def _control_in_tmp(monkeypatch, tmp_path):
    """Redirect ~/.bnbagent/control.json to a tmp file so tests don't
    fight the real control bus."""
    monkeypatch.setenv("BNBAGENT_CONTROL_FILE", str(tmp_path / "control.json"))


def test_request_restart_writes_marker():
    """request_restart() must persist the intent so the running agent
    can pick it up on its next heartbeat."""
    assert not control.is_restart_requested()
    control.request_restart(reason="dashboard button")
    assert control.is_restart_requested()


def test_request_restart_records_reason_and_timestamp():
    """The marker carries a reason (for the agent log) and a timestamp
    (for the dashboard 'restart requested at X' display)."""
    control.request_restart(reason="config change")
    raw = control.read_control()
    assert "restart" in raw, f"control.json missing 'restart' key: {raw}"
    r = raw["restart"]
    assert r.get("reason") == "config change"
    assert isinstance(r.get("requested_at"), (int, float))
    assert r["requested_at"] > 0


def test_clear_restart_request_consumes_the_marker():
    """The agent calls clear_restart_request() after acting on it so
    the next process boot doesn't see a stale request and loop."""
    control.request_restart(reason="x")
    assert control.is_restart_requested()
    control.clear_restart_request()
    assert not control.is_restart_requested()


def test_clear_restart_request_preserves_other_control_fields():
    """Clearing the restart marker must NOT wipe unrelated control
    state (kill_switch, sleeve overrides, etc.) — those have their own
    lifecycle and apply_control() owns them."""
    # Seed unrelated control state first.
    control.write_control({"kill": True, "kill_reason": "manual",
                            "sleeves": {"A": False}})
    control.request_restart(reason="x")
    control.clear_restart_request()
    raw = control.read_control()
    assert raw.get("kill") is True
    assert raw.get("kill_reason") == "manual"
    assert raw.get("sleeves") == {"A": False}
    assert "restart" not in raw


def test_request_restart_default_reason():
    """Reason is optional; default is a short generic string."""
    control.request_restart()
    raw = control.read_control()
    assert raw["restart"].get("reason"), "default reason must be non-empty"


def test_repeated_request_restart_updates_timestamp_not_creates_list():
    """Second request just refreshes the marker — there's no queue."""
    control.request_restart(reason="first")
    ts1 = control.read_control()["restart"]["requested_at"]
    # Force a different second.
    import time as _t; _t.sleep(0.01)
    control.request_restart(reason="second")
    r2 = control.read_control()["restart"]
    assert r2["reason"] == "second"
    assert r2["requested_at"] >= ts1


def test_is_restart_requested_handles_missing_file():
    """Cold start: no control.json yet → not requested."""
    assert not control.is_restart_requested()
