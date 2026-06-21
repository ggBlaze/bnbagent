"""Portfolio — equity, drawdown, PnL, position mgmt."""
import time
from decimal import Decimal

import pytest

from core.portfolio import Portfolio, Position


def make_pos(symbol="ETH", notional=Decimal("10"), risk=Decimal("0.5"), side="long", entry=Decimal("100"), sleeve="B"):
    return Position(
        sleeve=sleeve, symbol=symbol, side=side,
        notional_usdc=notional, risk_usdc=risk,
        entry_ts=int(time.time()), entry_price=entry,
        stop_price=entry * Decimal("0.98"), tp_price=entry * Decimal("1.03"),
    )


class TestPortfolio:
    def test_initial_equity(self):
        p = Portfolio(starting_equity=Decimal("100"))
        assert p.equity() == Decimal("100")
        assert p.peak_equity == Decimal("100")
        assert p.drawdown_pct() == 0.0

    def test_add_position_reduces_cash(self):
        p = Portfolio(starting_equity=Decimal("100"))
        p.add_position("B:ETH", make_pos(notional=Decimal("10")))
        # cash = 100 - 10 = 90, plus unrealized ≈ 0
        assert p.cash_usdc == Decimal("90")

    def test_close_position_realizes_pnl(self):
        p = Portfolio(starting_equity=Decimal("100"))
        p.add_position("B:ETH", make_pos(notional=Decimal("10"), entry=Decimal("100")))
        p.close_position("B:ETH", exit_price=Decimal("103"), reason="tp_hit")
        assert len(p.closed_trades) == 1
        trade = p.closed_trades[0]
        assert trade["reason"] == "tp_hit"
        assert float(trade["pnl_usdc"]) > 0   # 3% on $10 = $0.30

    def test_long_pnl(self):
        p = Portfolio(starting_equity=Decimal("100"))
        pos = make_pos(notional=Decimal("10"), entry=Decimal("100"))
        # simulate price going to 102
        pos.extra["mark_provider"] = lambda: Decimal("102")
        # call mark_to_market directly
        pnl = pos.mark_to_market(Decimal("102"))
        assert abs(float(pnl) - 0.2) < 0.001    # 2% of $10

    def test_short_pnl(self):
        pos = make_pos(notional=Decimal("10"), entry=Decimal("100"), side="short")
        pnl = pos.mark_to_market(Decimal("98"))
        assert abs(float(pnl) - 0.2) < 0.001    # 2% of $10

    def test_max_drawdown(self):
        p = Portfolio(starting_equity=Decimal("100"))
        for eq in [110, 105, 95, 90, 100, 102]:
            p.equity_history.append((int(time.time()), Decimal(str(eq))))
        # peak = 110, trough = 90, mdd = 18.18%
        mdd = p.max_drawdown_pct()
        assert 18.0 < mdd < 19.0

    def test_sleeve_exposure(self):
        p = Portfolio(starting_equity=Decimal("100"))
        p.add_position("A:ETH", make_pos(notional=Decimal("10"), sleeve="A"))
        p.add_position("B:SOL", make_pos(notional=Decimal("5"), sleeve="B"))
        p.add_position("C:LINK", make_pos(notional=Decimal("2"), sleeve="C"))
        assert p.sleeve_exposure("A") == Decimal("10")
        assert p.sleeve_exposure("B") == Decimal("5")
        assert p.sleeve_exposure("C") == Decimal("2")

    def test_gross_exposure(self):
        p = Portfolio(starting_equity=Decimal("100"))
        p.add_position("A:ETH", make_pos(notional=Decimal("10")))
        p.add_position("B:SOL", make_pos(notional=Decimal("5")))
        assert p.gross_exposure() == Decimal("15")

    def test_stats(self):
        p = Portfolio(starting_equity=Decimal("100"))
        p.update_peak()
        s = p.stats()
        assert s["starting"] == 100.0
        assert s["open_positions"] == 0
        assert s["closed_trades"] == 0
        assert s["sleeve_exposure"] == {"A": 0.0, "B": 0.0, "C": 0.0}

    def test_day_pnl_pct_positive_on_gain(self):
        """day_pnl_pct must be POSITIVE when equity ends ABOVE day_start.

        Bug: the previous formula was `(ds - e) / ds * 100`, which inverts
        the sign — a +25% gain showed as -25%.
        """
        p = Portfolio(starting_equity=Decimal("100"))
        # Force a synthetic history: day started at 80, now equity is 100.
        # Simulate by setting cash directly + pinning day_start.
        p.cash_usdc = Decimal("100")
        p.day_start_equity[p._today()] = Decimal("80")
        assert p.day_pnl_pct() == pytest.approx(25.0)

    def test_day_pnl_pct_negative_on_loss(self):
        """day_pnl_pct must be NEGATIVE when equity ends BELOW day_start."""
        p = Portfolio(starting_equity=Decimal("100"))
        p.cash_usdc = Decimal("80")
        p.day_start_equity[p._today()] = Decimal("100")
        assert p.day_pnl_pct() == pytest.approx(-20.0)
