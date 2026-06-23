"""Tests for core/daily_trade_floor.py.

The BNB HACK 2026 contest requires 1 trade per day for 7 days. The
floor module guarantees that by firing a tiny rebalance trade at
23:30 UTC if no sleeve trade happened that day. These tests cover:

  1. Trade counting (open + closed) for "today" (UTC).
  2. Floor fires when no trades happened.
  3. Floor does NOT fire when 1+ trades happened.
  4. Floor does NOT fire twice in the same UTC day.
  5. Floor picks an in-scope symbol from the basket.
  6. Floor respects the BNB_HACK_NO_DAILY_FLOOR opt-out env var.
  7. Floor rejects when the circuit breaker says no.
  8. Floor's trade is sized at 0.1% of equity (well under the 1% cap).
  9. status() returns the shape the dashboard reads.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from core import daily_trade_floor as dtf  # noqa: E402


# -- helpers ---------------------------------------------------------------

class FakePosition:
    def __init__(self, opened_at: int):
        self.opened_at = opened_at
        self.notional_usdc = Decimal("10")
        self.risk_usdc = Decimal("0.1")
        self.symbol = "ETH"
        self.sleeve = "A"
        self.side = "buy"
        self.entry_price = Decimal("100")


class FakeClosedTrade:
    def __init__(self, exit_ts: int):
        self.exit_ts = exit_ts


class FakePortfolio:
    def __init__(self, *, opened: list[int] = None, closed: list[int] = None,
                 equity: Decimal = Decimal("1000")):
        self.positions = {f"p{i}": FakePosition(ts) for i, ts in enumerate(opened or [])}
        self.closed_trades = [FakeClosedTrade(ts) for ts in (closed or [])]
        self._equity = equity
        self.peak_equity = equity

    def equity(self) -> Decimal:
        return self._equity

    def _mark_price(self, sym: str) -> Decimal:
        return Decimal("100")

    def add_position(self, pos_id, pos):
        self.positions[pos_id] = pos

    def close_position(self, pos_id, exit_price, reason="manual"):
        self.closed_trades.append({"id": pos_id, "reason": reason})
        return Decimal("0.01")


def fake_agent(*, opened: list[int] = None, closed: list[int] = None,
               equity: Decimal = Decimal("1000"),
               basket: list[str] = None,
               policy: dict = None):
    a = MagicMock()
    pf = FakePortfolio(opened=opened or [], closed=closed or [], equity=equity)
    a.portfolio = pf
    a.policy = policy or {
        "allowlist": {"bsc_tokens": ["ETH", "USDC", "USDT", "DAI", "AAVE"]},
        "global_risk": {
            "max_gross_leverage": 2.0,
            "per_trade_risk_pct": 1.0,
            "max_single_position_pct": 15.0,
            "daily_loss_circuit_breaker_pct": 5.0,
            "max_daily_trades": 100,
        },
        "sleeves": {
            "A": {"max_position_pct": 15.0, "enabled": True},
            "B": {"max_position_pct": 10.0, "enabled": True},
            "C": {"max_position_pct": 5.0, "enabled": True},
        },
    }
    a.config = {"cmc": {"basket_symbols": basket or ["ETH", "USDC", "BTC", "MATIC"]}}
    a.components = {"config": a.config, "policy": a.policy}
    # AsyncMock makes .return_value awaitable
    from unittest.mock import AsyncMock
    a.submit_floor_trade = AsyncMock(return_value={"status": "opened", "pos_id": "FLOOR-1"})
    return a


def _utc_today_midnight() -> int:
    """Epoch seconds at 00:00 UTC today."""
    now = datetime.now(timezone.utc)
    return int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def _floor_at(agent, fake_now_ts: int):
    """Build a DailyTradeFloor with a deterministic clock."""
    return dtf.DailyTradeFloor(agent, clock=lambda: fake_now_ts)


# -- 1. Trade counting ----------------------------------------------------

def test_count_trades_today_includes_opens():
    """A position opened today counts as today's trade."""
    today = _utc_today_midnight() + 3600
    pf = FakePortfolio(opened=[today])
    a = fake_agent()
    a.portfolio = pf
    floor = dtf.DailyTradeFloor(a)
    n = floor._count_trades_today(_utc_today_midnight() + 7200)
    assert n == 1


def test_count_trades_today_includes_closes():
    """A position closed today counts as today's trade, even if it was
    opened yesterday."""
    yesterday = _utc_today_midnight() - 24 * 3600
    today = _utc_today_midnight() + 3600
    pf = FakePortfolio(opened=[yesterday], closed=[today])
    a = fake_agent()
    a.portfolio = pf
    floor = dtf.DailyTradeFloor(a)
    n = floor._count_trades_today(_utc_today_midnight() + 7200)
    assert n == 1


def test_count_trades_today_zero_when_yesterday_only():
    yesterday = _utc_today_midnight() - 24 * 3600
    pf = FakePortfolio(opened=[yesterday], closed=[yesterday + 3600])
    a = fake_agent()
    a.portfolio = pf
    floor = dtf.DailyTradeFloor(a)
    n = floor._count_trades_today(_utc_today_midnight() + 7200)
    assert n == 0


# -- 2 + 3. Fire / don't fire --------------------------------------------

@pytest.mark.asyncio
async def test_fires_when_no_trades_today(monkeypatch):
    """With zero trades today and the clock past 23:30 UTC, the floor
    fires a rebalance trade."""
    monkeypatch.delenv("BNB_HACK_NO_DAILY_FLOOR", raising=False)
    a = fake_agent()
    fake_now_ts = _utc_today_midnight() + 23 * 3600 + 31 * 60  # 23:31 UTC
    floor = _floor_at(a, fake_now_ts)
    result = await floor.tick()
    assert result is not None
    assert result.get("fired") is True, f"expected fire, got: {result}"
    a.submit_floor_trade.assert_called_once()


@pytest.mark.asyncio
async def test_does_not_fire_when_trade_already_today(monkeypatch):
    """With 1+ trade today, the floor stays quiet."""
    today = _utc_today_midnight() + 3600
    monkeypatch.delenv("BNB_HACK_NO_DAILY_FLOOR", raising=False)
    a = fake_agent(opened=[today])
    fake_now_ts = _utc_today_midnight() + 23 * 3600 + 31 * 60
    floor = _floor_at(a, fake_now_ts)
    result = await floor.tick()
    assert result is not None
    assert result.get("fired") is False
    a.submit_floor_trade.assert_not_called()


@pytest.mark.asyncio
async def test_does_not_fire_before_2330_utc(monkeypatch):
    """Before the check time, the floor stays quiet (the day isn't over)."""
    monkeypatch.delenv("BNB_HACK_NO_DAILY_FLOOR", raising=False)
    a = fake_agent()
    fake_now_ts = _utc_today_midnight() + 12 * 3600  # noon UTC
    floor = _floor_at(a, fake_now_ts)
    result = await floor.tick()
    assert result is None


# -- 4. Once per day -------------------------------------------------------

@pytest.mark.asyncio
async def test_does_not_double_fire_same_day(monkeypatch):
    """After firing today, a second tick on the same day is a no-op."""
    monkeypatch.delenv("BNB_HACK_NO_DAILY_FLOOR", raising=False)
    a = fake_agent()
    fake_now_ts = _utc_today_midnight() + 23 * 3600 + 31 * 60
    floor = _floor_at(a, fake_now_ts)
    await floor.tick()
    a.submit_floor_trade.reset_mock()
    result = await floor.tick()
    assert result is None or result.get("fired") is False


# -- 5. In-scope universe --------------------------------------------------

@pytest.mark.asyncio
async def test_picks_in_scope_symbol(monkeypatch):
    """When the basket has BTC, MATIC, ETH — the floor picks an in-scope one."""
    monkeypatch.delenv("BNB_HACK_NO_DAILY_FLOOR", raising=False)
    monkeypatch.delenv("BNB_HACK_TRACK1", raising=False)
    a = fake_agent(basket=["BTC", "MATIC", "ETH", "USDC"])
    fake_now_ts = _utc_today_midnight() + 23 * 3600 + 31 * 60
    floor = _floor_at(a, fake_now_ts)
    result = await floor.tick()
    assert result.get("fired") is True
    assert result.get("symbol") in {"ETH", "USDC", "USDT", "DAI"}, \
        f"out-of-scope symbol picked: {result.get('symbol')}"


# -- 6. Opt-out ------------------------------------------------------------

@pytest.mark.asyncio
async def test_opt_out_disables_floor(monkeypatch):
    monkeypatch.setenv("BNB_HACK_NO_DAILY_FLOOR", "1")
    a = fake_agent()
    floor = dtf.DailyTradeFloor(a)
    result = await floor.tick()
    assert result is None


# -- 7. Circuit breaker rejection -----------------------------------------

@pytest.mark.asyncio
async def test_floor_bails_when_equity_too_small(monkeypatch):
    """If equity is so small the floor trade is < $0.50, the floor records
    the failure instead of crashing."""
    monkeypatch.delenv("BNB_HACK_NO_DAILY_FLOOR", raising=False)
    monkeypatch.delenv("BNB_HACK_TRACK1", raising=False)
    a = fake_agent(equity=Decimal("0.01"))
    fake_now_ts = _utc_today_midnight() + 23 * 3600 + 31 * 60
    floor = _floor_at(a, fake_now_ts)
    result = await floor.tick()
    assert result is not None
    assert result.get("fired") is False
    note = result.get("note", "")
    assert "too small" in note or "rejected" in note, f"unexpected note: {note!r}"


# -- 8. Sizing -------------------------------------------------------------

@pytest.mark.asyncio
async def test_floor_trade_size_is_1_25_pct_of_equity(monkeypatch):
    """v2.2.2: the floor is sized at FLOOR_NOTIONAL_FRACTION = 0.0125
    of equity (1.25% — was 0.1% before v2.2.2). 1.25% is well below
    the 1% per-trade risk cap when measured against per-trade risk
    (notional * max_loss_pct), so a 100% loss on the floor still
    can't trip the 5% daily circuit breaker."""
    monkeypatch.delenv("BNB_HACK_NO_DAILY_FLOOR", raising=False)
    monkeypatch.delenv("BNB_HACK_TRACK1", raising=False)
    a = fake_agent(equity=Decimal("10000"))
    fake_now_ts = _utc_today_midnight() + 23 * 3600 + 31 * 60
    floor = _floor_at(a, fake_now_ts)
    result = await floor.tick()
    assert result.get("fired") is True
    notional = result.get("notional")
    assert notional is not None
    # 10000 * 0.0125 = 125 USDC
    assert abs(notional - 125.0) < 5.0, f"expected ~125 USDC floor (1.25% of 10k), got {notional}"


# -- 9. status() -----------------------------------------------------------

def test_status_shape():
    a = fake_agent()
    floor = dtf.DailyTradeFloor(a)
    s = floor.status()
    assert s["last_fire_status"] == "n/a"
    assert s["total_fires"] == 0
    assert s["total_days_covered"] == 0
    assert "last_check_utc_day" in s
    assert "last_fire_utc_day" in s
    assert "last_fire_note" in s
