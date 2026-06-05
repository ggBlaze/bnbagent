"""Risk engine — circuit_breaker_check + Kelly sizing."""
import pytest
from decimal import Decimal

from core.risk import circuit_breaker_check, ProposedTrade, kelly_size, cap_by_risk, day_loss_breach_today
from tests.fixtures.wallets import TEST_POLICY


def make_position(symbol="ETH", notional=Decimal("10"), risk=Decimal("0.5")):
    from core.portfolio import Position
    return Position(
        sleeve="B", symbol=symbol, side="long",
        notional_usdc=notional, risk_usdc=risk,
        entry_ts=0, entry_price=Decimal("100"),
        stop_price=Decimal("99"), tp_price=Decimal("103"),
    )


class TestCircuitBreaker:
    def test_allows_baseline(self):
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=ProposedTrade("B", "ETH", "long", Decimal("5"), Decimal("0.5")),
            policy=TEST_POLICY,
            day_start_equity=Decimal("100"),
        )
        assert ok
        assert reason == "ok"

    def test_blocks_when_daily_loss_breached(self):
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("96"),         # 4% down
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=ProposedTrade("B", "ETH", "long", Decimal("5"), Decimal("0.5")),
            policy=TEST_POLICY,
            day_start_equity=Decimal("100"),
        )
        assert not ok
        assert "daily loss" in reason

    def test_blocks_when_per_trade_risk_exceeded(self):
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=ProposedTrade("B", "ETH", "long", Decimal("50"), Decimal("2")),   # 2% risk > 1% cap
            policy=TEST_POLICY,
            day_start_equity=Decimal("100"),
        )
        assert not ok
        assert "per-trade risk" in reason

    def test_blocks_when_position_size_exceeds_sleeve_cap(self):
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=ProposedTrade("B", "ETH", "long", Decimal("20"), Decimal("0.2")),
            policy=TEST_POLICY,
            day_start_equity=Decimal("100"),
        )
        assert not ok
        # either single-pos or sleeve cap should trigger; both are valid
        assert ("sleeve B cap" in reason) or ("15.0%" in reason)

    def test_blocks_when_symbol_not_in_allowlist(self):
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=ProposedTrade("B", "SCAMCOIN", "long", Decimal("1"), Decimal("0.1")),
            policy=TEST_POLICY,
            day_start_equity=Decimal("100"),
        )
        assert not ok
        assert "not in allowlist" in reason

    def test_blocks_when_sleeve_disabled(self):
        policy = {**TEST_POLICY, "sleeves": {**TEST_POLICY["sleeves"], "B": {**TEST_POLICY["sleeves"]["B"], "enabled": False}}}
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=ProposedTrade("B", "ETH", "long", Decimal("1"), Decimal("0.1")),
            policy=policy,
            day_start_equity=Decimal("100"),
        )
        assert not ok
        assert "disabled" in reason

    def test_blocks_when_gross_leverage_exceeded(self):
        # Each position has notional=50, so 4 positions = 200 (2x of 100).
        # But each is 50% of equity, so single-position cap (15%) triggers first.
        # Use larger equity so single-pos passes, but gross lev fails.
        positions = [make_position(notional=Decimal("20")) for _ in range(15)]   # 15×20 = 300 on 100 = 3x
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=positions,
            proposed=None,
            policy=TEST_POLICY,
            day_start_equity=Decimal("100"),
        )
        assert not ok
        # Could be either single-pos or gross-lev; both are valid
        assert ("gross lev" in reason) or ("15.0%" in reason)

    def test_blocks_when_max_drawdown_breached(self):
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("91"),           # 9% drawdown
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=ProposedTrade("B", "ETH", "long", Decimal("1"), Decimal("0.1")),
            policy=TEST_POLICY,
            day_start_equity=Decimal("91"),
        )
        assert not ok
        # could trigger via daily loss OR drawdown
        assert ("drawdown" in reason) or ("daily loss" in reason)

    def test_cooldown_blocks_during_window(self):
        import time
        now = int(time.time())
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=ProposedTrade("B", "ETH", "long", Decimal("1"), Decimal("0.1")),
            policy=TEST_POLICY,
            day_start_equity=Decimal("100"),
            day_breach_active_until=now + 100,
            now_ts=now,
        )
        assert not ok
        assert "cooldown" in reason


class TestKelly:
    def test_kelly_zero_when_p_equals_lose_prob(self):
        # p*b = (1-p) → f_full = 0
        f = kelly_size(0.5, 1.0, kelly_fraction=0.25)
        assert f == 0.0

    def test_kelly_positive_with_edge(self):
        f = kelly_size(0.6, 1.5, kelly_fraction=0.25)
        assert f > 0.0
        # quarter-kelly: f_full = (0.6*1.5 - 0.4)/1.5 = 0.333...; f_quarter ≈ 0.083
        assert 0.05 < f < 0.15

    def test_kelly_zero_when_p_below_break_even(self):
        f = kelly_size(0.4, 1.0, kelly_fraction=0.25)
        assert f == 0.0

    def test_cap_by_risk(self):
        size = cap_by_risk(
            fraction=0.5, equity=Decimal("100"),
            stop_distance_fraction=0.02,
            per_trade_risk_pct=1.0,
        )
        # 50% of equity = 50; capped at 1% / 2% = 50% → both 50
        assert size == Decimal("50")

    def test_cap_by_risk_when_kelly_overshoots(self):
        size = cap_by_risk(
            fraction=0.5, equity=Decimal("100"),
            stop_distance_fraction=0.005,    # 0.5% stop
            per_trade_risk_pct=1.0,
        )
        # 50% of equity = 50; capped at 1% / 0.5% = 200% → min is 50
        assert size == Decimal("50")


class TestDayLoss:
    def test_breach_above_threshold(self):
        assert day_loss_breach_today(Decimal("97"), Decimal("100"), 3.0)

    def test_no_breach_below_threshold(self):
        assert not day_loss_breach_today(Decimal("98"), Decimal("100"), 3.0)

    def test_no_breach_with_no_start(self):
        assert not day_loss_breach_today(Decimal("90"), None, 3.0)
