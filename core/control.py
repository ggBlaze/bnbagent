"""Control bus — IPC between the dashboard backend and the running agent.

The dashboard writes intents to ~/.bnbagent/control.json; the agent reads it
once per heartbeat and applies them (kill switch, sleeve toggles, config
overrides). Writes are atomic (tmp + rename).
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path("~/.bnbagent/control.json").expanduser()


def _path() -> Path:
    p = Path(os.environ.get("BNBAGENT_CONTROL_FILE", str(DEFAULT_PATH)))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def read_control() -> dict:
    p = _path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def write_control(state: dict) -> None:
    """Atomically write the control file."""
    p = _path()
    fd, tmp = tempfile.mkstemp(prefix=".control-", dir=str(p.parent))
    try:
        os.write(fd, json.dumps(state, indent=2).encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, p)


def apply_control(policy: dict, portfolio) -> list[str]:
    """Apply pending control intents to the in-memory policy + portfolio.

    Returns a list of human-readable change lines (for the dashboard log).
    """
    c = read_control()
    if not c:
        return []
    msgs: list[str] = []

    # 1. Kill switch
    if c.get("kill") and not policy.get("_kill_switch"):
        policy["_kill_switch"] = True
        policy["_kill_reason"] = c.get("kill_reason", "manual from dashboard")
        portfolio.kill_switch = True
        portfolio.kill_reason = policy["_kill_reason"]
        msgs.append(f"🔴 KILL SWITCH engaged: {policy['_kill_reason']}")
    elif c.get("resume") and policy.get("_kill_switch"):
        policy.pop("_kill_switch", None)
        policy.pop("_kill_reason", None)
        portfolio.kill_switch = False
        portfolio.kill_reason = ""
        msgs.append("🟢 kill switch cleared — agent resumed")

    # 2. Sleeve toggles
    sleeve_overrides = c.get("sleeves") or {}
    if sleeve_overrides and "sleeves" in policy:
        for name, on in sleeve_overrides.items():
            if name in policy["sleeves"]:
                was = policy["sleeves"][name].get("enabled", True)
                policy["sleeves"][name]["enabled"] = bool(on)
                if was != bool(on):
                    msgs.append(f"sleeve {name} → {'enabled' if on else 'DISABLED'}")

    # 3. Global risk overrides (only numbers, only known keys)
    risk_overrides = c.get("global_risk") or {}
    if risk_overrides and "global_risk" in policy:
        for k, v in risk_overrides.items():
            if isinstance(v, (int, float)) and k in policy["global_risk"]:
                old = policy["global_risk"][k]
                if old != v:
                    policy["global_risk"][k] = float(v)
                    msgs.append(f"risk.{k}: {old} → {v}")

    if msgs:
        # bump version so observers can see it. Use the portfolio's
        # injected clock (v2.0.7) instead of int(time.time()) so the
        # replay harness — which routes the clock through the
        # current tape ts — produces deterministic audit lines. In
        # production the clock falls back to wall-clock.
        c["_applied_at"] = portfolio._now()
        c["_applied_lines"] = msgs
        write_control(c)
    return msgs


# --- v2.1.8 (A): restart-agent flow ---------------------------------------
#
# Dashboard POST /api/agent/restart → request_restart() writes a marker.
# Agent heartbeat → is_restart_requested() polls; on True it clears the
# marker, sets _restart_pending, triggers graceful shutdown. core.main
# exits with code 75 when _restart_pending. The bash wrapper loops on
# exit 75 to re-exec the agent process.
#
# The marker lives under a separate "restart" key on the same control
# file so apply_control() doesn't have to know about it (and so the
# existing kill/sleeve/risk fields aren't disturbed by clear).

_DEFAULT_RESTART_REASON = "manual restart request"


def request_restart(reason: str | None = None) -> None:
    """Write a restart marker to the control file.

    Idempotent: a second call updates the timestamp + reason; the agent
    only consumes one restart per heartbeat tick anyway.
    """
    c = read_control()
    c["restart"] = {
        "reason": reason or _DEFAULT_RESTART_REASON,
        "requested_at": time.time(),
    }
    write_control(c)


def is_restart_requested() -> bool:
    """True if a restart has been requested and not yet consumed."""
    return bool(read_control().get("restart"))


def clear_restart_request() -> None:
    """Consume the marker. Preserves all other control fields so an
    in-flight kill/sleeve override survives the restart cycle."""
    c = read_control()
    if "restart" in c:
        del c["restart"]
        write_control(c)


# --- v2.2.0: manual force-fire the daily trade floor ------------------
#
# Operator writes {"force_fire_floor": true, "reason": "..."} to
# control.json. The daily_floor's tick() consumes it on the next
# heartbeat (within 1s), bypasses the (hour, minute) gate and the
# "already checked today" throttle, and calls _fire_floor_trade().
# The trade appears in the portfolio + the on-chain tx lands in
# BscTrace. Useful when the operator wants to verify the on-chain
# path is healthy without waiting until 23:30 UTC.
#
# One-shot: the marker is consumed and removed from the control
# file. A second fire requires writing it again.

def request_force_fire_floor(reason: str = "manual force-fire") -> None:
    """Write a force-fire marker to the control file."""
    c = read_control()
    c["force_fire_floor"] = {"reason": reason, "requested_at": time.time()}
    write_control(c)


def _consume_force_fire() -> bool:
    """True iff a force-fire was requested AND we just consumed it.
    Atomically removes the marker so a 1s-tick heart beat doesn't
    re-fire."""
    c = read_control()
    if not c.get("force_fire_floor"):
        return False
    if "force_fire_floor" in c:
        del c["force_fire_floor"]
        write_control(c)
    return True

