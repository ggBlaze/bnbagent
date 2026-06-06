"""Control bus — IPC between the dashboard backend and the running agent.

The dashboard writes intents to ~/.bnbagent/control.json; the agent reads it
once per heartbeat and applies them (kill switch, sleeve toggles, config
overrides). Writes are atomic (tmp + rename).
"""
from __future__ import annotations

import json
import os
import tempfile
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
