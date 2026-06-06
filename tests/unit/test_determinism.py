"""Replay determinism — the v2.0.4 audit finding.

Before v2.0.4, the replay harness used wall-clock time (int(time.time()))
and Python's random.random() in three places, so two consecutive runs
produced different numbers. The meta-test that locks the demo-script
table to the JSON would have failed intermittently.

After v2.0.4, an injected clock advances to the candle's ts on every
tick, and all random sources are seeded or removed. This test runs the
replay twice and asserts the resulting metrics are identical.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


def _run_replay(regime: str) -> dict:
    from decimal import Decimal
    from backtest.replay import make_synthetic_week, run_replay
    tape = make_synthetic_week(seed=42, regime=regime)
    return asyncio.run(
        run_replay(
            tape_path=None,
            report_path=f"/tmp/replay_{regime}_det.html",
            equity=1000.0,
            tape=tape,
            open_jobs_flag=False,
        )
    )


def _metrics_signature(m: dict) -> dict:
    """Reduce a metrics dict to fields that should be identical across
    deterministic runs. Float comparisons use a tight tolerance."""
    return {
        "trades": m["trades"],
        "total_return_pct": round(m["total_return_pct"], 6),
        "max_drawdown_pct": round(m["max_drawdown_pct"], 6),
        "hit_rate": round(m["hit_rate"], 6),
        "sharpe": round(m["sharpe"], 4),
        "sortino": round(m["sortino"], 4),
    }


@pytest.mark.parametrize("regime", ["bull", "bear", "chop"])
def test_replay_is_deterministic(regime):
    """Run the same regime twice and assert the metrics match exactly.
    Catches any new int(time.time()), random.random(), or hash() that
    creeps into the strategies, portfolio, or perps."""
    a = _run_replay(regime)
    b = _run_replay(regime)
    sig_a = _metrics_signature(a)
    sig_b = _metrics_signature(b)
    assert sig_a == sig_b, (
        f"replay non-deterministic for regime={regime}:\n"
        f"  run 1: {sig_a}\n"
        f"  run 2: {sig_b}\n"
        f"Audit finding from v2.0.4 review — make sure the only "
        f"non-determinism sources (int(time.time()), random.random(), "
        f"hash() with PYTHONHASHSEED) are replaced with the injected "
        f"self.clock() and seeded RNGs."
    )


def test_clock_injection_actually_used():
    """The clock parameter must be threaded through to the strategies
    and perps. Verify by constructing a Portfolio with a custom clock
    and confirming _now() returns the clock's value."""
    from core.portfolio import Portfolio
    custom = [1234567890.0]
    pf = Portfolio(clock=lambda: custom[0])
    assert pf._now() == 1234567890
    custom[0] = 9999999999.0
    assert pf._now() == 9999999999
