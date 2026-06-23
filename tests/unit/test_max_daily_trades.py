"""Tests for v2.3.4: max_daily_trades cap enforcement + Portfolio counter.

The cap is the contest tuning knob — `policy.global_risk.max_daily_trades`.
It blocks a proposed OPEN once today's open count has hit the cap. Closes
(`is_new=False`) are not gated. The daily-trade-floor fires with
`is_floor=True` so the contest 1-trade/day guarantee isn't blocked by the
sleeves using all their slots.

Mirrors the daily_loss_circuit_breaker test pattern in test_risk.py.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from core.portfolio import Portfolio, Position
from core.risk import ProposedTrade, circuit_breaker_check
from tests.fixtures.wallets import TEST_POLICY


def _pos(symbol: str = "ETH", notional: Decimal = Decimal("1")) -> Position:
    """Tiny position — notional is intentionally small so the per-trade-risk
    and gross-leverage checks don't fire before the daily-trade-cap check."""
    return Position(
        sleeve="B", symbol=symbol, side="long",
        notional_usdc=notional, risk_usdc=Decimal("0.01"),
        entry_ts=0, entry_price=Decimal("100"),
        stop_price=Decimal("99"), tp_price=Decimal("103"),
    )


def _open(portfolio: Portfolio, n: int, symbol_prefix: str = "T") -> None:
    """Open `n` positions so `trades_opened_today` reads as `n`."""
    for i in range(n):
        portfolio.add_position(f"B:{symbol_prefix}{i}", _pos(symbol=f"{symbol_prefix}{i}"))


def _policy_with_cap(cap: int | None) -> dict:
    """Build a TEST_POLICY-shaped dict with the daily-trade-cap set/cleared."""
    policy = {**TEST_POLICY}
    policy["global_risk"] = {**TEST_POLICY["global_risk"]}
    if cap is None:
        policy["global_risk"].pop("max_daily_trades", None)
    else:
        policy["global_risk"]["max_daily_trades"] = cap
    return policy


def _proposed_open(symbol: str = "ETH", notional: Decimal = Decimal("1")) -> ProposedTrade:
    return ProposedTrade(
        sleeve="B", symbol=symbol, side="long",
        notional_usdc=notional, risk_usdc=Decimal("0.01"),
        is_new=True,
    )


def _proposed_close(symbol: str = "ETH", notional: Decimal = Decimal("1")) -> ProposedTrade:
    return ProposedTrade(
        sleeve="B", symbol=symbol, side="long",
        notional_usdc=notional, risk_usdc=Decimal("0.01"),
        is_new=False,
    )


# ------------------------------------------------------------------
# 1. Portfolio counter: increment + accessor
# ------------------------------------------------------------------

class TestPortfolioCounter:
    def test_add_position_increments_today(self):
        pf = Portfolio(starting_equity=Decimal("100"))
        assert pf.trades_opened_today_str() == 0
        _open(pf, 3)
        assert pf.trades_opened_today_str() == 3

    def test_close_does_not_increment(self):
        pf = Portfolio(starting_equity=Decimal("100"))
        pf.add_position("B:X", _pos("X"))
        before = pf.trades_opened_today_str()
        pf.close_position("B:X", Decimal("101"), reason="tp")
        assert pf.trades_opened_today_str() == before

    def test_stats_exposes_trades_opened_today(self):
        pf = Portfolio(starting_equity=Decimal("100"))
        _open(pf, 2)
        s = pf.stats()
        assert s["trades_opened_today"] == 2

    def test_day_rollover_at_midnight_utc(self, monkeypatch):
        """At 23:59:59 UTC we open 2; at 00:00:01 UTC we open 1 more;
        today's count must be 1 (not 3) because the day boundary reset
        the counter."""
        import time as _t
        pf = Portfolio(starting_equity=Decimal("100"))

        # Freeze "today" to a specific date; open 2 positions under it.
        fixed_today = "2026-06-23"
        monkeypatch.setattr(
            "core.portfolio.time.strftime",
            lambda fmt, _t_struct=_t.gmtime(0): (
                fixed_today if "%Y-%m-%d" in fmt
                else _t.strftime(fmt, _t_struct)
            ),
        )
        # Use the public API path (the helper calls time.strftime)
        _open(pf, 2, symbol_prefix="A")
        assert pf.trades_opened_today_str() == 2

        # Roll the "today" string forward; next open must start at 1, not 3.
        monkeypatch.setattr(
            "core.portfolio.time.strftime",
            lambda fmt, _t_struct=_t.gmtime(0): (
                "2026-06-24" if "%Y-%m-%d" in fmt
                else _t.strftime(fmt, _t_struct)
            ),
        )
        # Re-seed via the helper — this matches the lazy-seed path on
        # _maybe_seed_day.
        pf._maybe_seed_day()
        pf.add_position("B:NEW", _pos("NEW"))
        assert pf.trades_opened_today_str() == 1
        # The previous day's count is still preserved (history), not wiped.
        assert pf.trades_opened_today["2026-06-23"] == 2


# ------------------------------------------------------------------
# 2. Risk gate: cap blocks opens at threshold
# ------------------------------------------------------------------

class TestDailyTradeCap:
    def test_blocks_open_when_cap_reached(self):
        policy = _policy_with_cap(cap=3)
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=_proposed_open(),
            policy=policy,
            day_start_equity=Decimal("100"),
            trades_opened_today=3,        # already at cap
        )
        assert not ok
        assert "daily trade cap reached" in reason
        assert "3/3" in reason

    def test_allows_open_below_cap(self):
        policy = _policy_with_cap(cap=3)
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=_proposed_open(),
            policy=policy,
            day_start_equity=Decimal("100"),
            trades_opened_today=2,
        )
        assert ok, reason

    def test_cap_does_not_block_closes(self):
        """Closing a position (is_new=False) MUST NOT consume a slot.
        Otherwise the cap would silently trap open positions forever."""
        policy = _policy_with_cap(cap=3)
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[_pos()],      # one position open
            proposed=_proposed_close(),   # is_new=False
            policy=policy,
            day_start_equity=Decimal("100"),
            trades_opened_today=3,        # sleeves are at cap
        )
        assert ok, reason

    def test_unlimited_when_cap_missing(self):
        """Backwards-compat: if max_daily_trades is not in the policy
        (legacy YAML or test fixture), the check must NOT block — the
        pre-v2.3.4 behavior was effectively no cap."""
        policy = _policy_with_cap(cap=None)
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=_proposed_open(),
            policy=policy,
            day_start_equity=Decimal("100"),
            trades_opened_today=9999,     # wild counter
        )
        assert ok, reason

    def test_unlimited_when_cap_is_zero(self):
        """cap=0 → effectively no cap (mirrors the `if cap` truthiness
        check). Setting cap=0 from the dashboard means 'unlimited'."""
        policy = _policy_with_cap(cap=0)
        ok, _ = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=_proposed_open(),
            policy=policy,
            day_start_equity=Decimal("100"),
            trades_opened_today=5000,
        )
        assert ok

    def test_cap_higher_than_counter_allows(self):
        policy = _policy_with_cap(cap=10)
        ok, _ = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=_proposed_open(),
            policy=policy,
            day_start_equity=Decimal("100"),
            trades_opened_today=5,
        )
        assert ok

    def test_cap_runs_before_daily_loss_check(self):
        """When BOTH the daily-trade-cap AND the daily-loss breaker would
        block, the cap must be the reported reason (it runs first in the
        check order). This is intentional — contest cap is a louder
        signal than a routine 3% daily loss."""
        policy = _policy_with_cap(cap=2)
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("96"),    # 4% loss → also trips breaker
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=_proposed_open(),
            policy=policy,
            day_start_equity=Decimal("100"),
            trades_opened_today=2,           # at cap
        )
        assert not ok
        assert "daily trade cap" in reason


# ------------------------------------------------------------------
# 3. Daily-trade-floor exemption
# ------------------------------------------------------------------

class TestFloorExemption:
    def test_floor_bypasses_cap(self):
        """The daily-trade-floor is the BNB HACK 1-trade/day guarantee.
        It must fire even if the sleeves have used all N daily slots —
        otherwise the agent is disqualified from the contest for missing
        the daily minimum."""
        policy = _policy_with_cap(cap=3)
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=_proposed_open(),
            policy=policy,
            day_start_equity=Decimal("100"),
            trades_opened_today=3,           # sleeves are at cap
            is_floor=True,                  # but this is the floor
        )
        assert ok, f"floor must bypass the cap; got reason={reason!r}"

    def test_non_floor_still_blocked_at_cap(self):
        """Sanity check: without is_floor=True, the cap still blocks at 3."""
        policy = _policy_with_cap(cap=3)
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=_proposed_open(),
            policy=policy,
            day_start_equity=Decimal("100"),
            trades_opened_today=3,
            is_floor=False,
        )
        assert not ok
        assert "daily trade cap" in reason


# ------------------------------------------------------------------
# 4. Integration: Portfolio counter feeds the gate
# ------------------------------------------------------------------

class TestEndToEnd:
    def test_counter_drives_gate_decision(self):
        """End-to-end: open positions through Portfolio.add_position,
        then read trades_opened_today_str() and feed it into the gate.
        Confirms the wiring in core/tick.py uses the right value."""
        pf = Portfolio(starting_equity=Decimal("100"))
        policy = _policy_with_cap(cap=2)

        # Open 1 → counter=1 → 2nd open allowed
        _open(pf, 1)
        ok, _ = circuit_breaker_check(
            current_equity=pf.equity(), peak_equity=pf.peak_equity,
            open_positions=list(pf.positions.values()),
            proposed=_proposed_open(symbol="CAKE"),
            policy=policy,
            day_start_equity=pf.day_start_equity[pf._today()],
            trades_opened_today=pf.trades_opened_today_str(),
        )
        assert ok

        # Open 2nd → counter=2 → at cap → 3rd blocked
        _open(pf, 1, symbol_prefix="S")
        ok, reason = circuit_breaker_check(
            current_equity=pf.equity(), peak_equity=pf.peak_equity,
            open_positions=list(pf.positions.values()),
            proposed=_proposed_open(symbol="USDC"),
            policy=policy,
            day_start_equity=pf.day_start_equity[pf._today()],
            trades_opened_today=pf.trades_opened_today_str(),
        )
        assert not ok
        assert "daily trade cap" in reason
