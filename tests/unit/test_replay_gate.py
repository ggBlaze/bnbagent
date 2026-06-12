"""Test the replay breach gate that drives the bnbagent exit code.

bnbagent reads the JSON sidecar and exits 1 if sharpe < 0 OR
max_drawdown_pct > 8 OR breaches > 0. We lock that gate logic here
so a tweak to the threshold (or the JSON shape) is caught at test time,
not in CI.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


GATE_PY = """
import json, sys
m = json.load(open(sys.argv[1]))
breach = (
    (m.get("sharpe", 0.0) < 0.0)
    or (m.get("max_drawdown_pct", 0.0) > 8.0)
    or (m.get("breaches", 0) > 0)
)
sys.exit(1 if breach else 0)
"""


def _write_metrics(path: Path, **kwargs) -> Path:
    """Write a metrics JSON sidecar with the given fields."""
    base = {
        "sharpe": 1.0, "max_drawdown_pct": 2.0, "breaches": 0,
        "kill_switch_engaged": False,
    }
    base.update(kwargs)
    path.write_text(json.dumps(base))
    return path


def test_gate_passes_when_clean(tmp_path):
    p = _write_metrics(tmp_path / "r.json", sharpe=2.0, max_drawdown_pct=1.5, breaches=0)
    r = subprocess.run(
        [sys.executable, "-c", GATE_PY, str(p)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr


def test_gate_fails_on_negative_sharpe(tmp_path):
    p = _write_metrics(tmp_path / "r.json", sharpe=-0.5, max_drawdown_pct=1.0)
    r = subprocess.run(
        [sys.executable, "-c", GATE_PY, str(p)],
        capture_output=True, text=True,
    )
    assert r.returncode == 1


def test_gate_fails_on_max_dd_over_8_pct(tmp_path):
    p = _write_metrics(tmp_path / "r.json", sharpe=2.0, max_drawdown_pct=8.5)
    r = subprocess.run(
        [sys.executable, "-c", GATE_PY, str(p)],
        capture_output=True, text=True,
    )
    assert r.returncode == 1


def test_gate_fails_on_any_breach(tmp_path):
    p = _write_metrics(tmp_path / "r.json", sharpe=2.0, max_drawdown_pct=1.0, breaches=1)
    r = subprocess.run(
        [sys.executable, "-c", GATE_PY, str(p)],
        capture_output=True, text=True,
    )
    assert r.returncode == 1


def test_gate_passes_at_exact_dd_threshold(tmp_path):
    """max_dd = 8.0 is the boundary; must NOT trip (> 8, not >= 8)."""
    p = _write_metrics(tmp_path / "r.json", sharpe=2.0, max_drawdown_pct=8.0)
    r = subprocess.run(
        [sys.executable, "-c", GATE_PY, str(p)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
