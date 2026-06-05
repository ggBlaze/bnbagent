"""Sleeve B — ATR rescale on vol-spike.

If realized vol doubles mid-trade, the static 2*ATR stop is too tight.
This test asserts the rescale logic in _monitor_open_positions widens
the stop when current_atr > entry_atr * vol_spike_threshold, and
clamps it to max_loss_pct.
"""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from strategies.sleeve_b_momentum import SleeveBMomentum
from core.portfolio import Position


def _make_components() -> dict:
    cfg = {
        "cmc": {"dex_universe_symbols": ["BTC", "ETH", "SOL"]},
        "dex": {"pcs_v3_router": "0x" + "1" * 40},
        "gas": {"swap_gas": 200000},
        "chain_id": 97,
    }
    policy = {
        "global_risk": {"per_trade_risk_pct": 1.0, "max_drawdown_pct": 10.0},
        "sleeves": {
            "B": {
                "enabled": True,
                "volume_spike_mult": 2.0,
                "breakout_lookback_h": 4,
                "atr_len": 14,
                "atr_stop_mult": 2.0,
                "tp_pct": 3.0,
                "max_hold_min": 240,
                "kelly_fraction": 0.25,
                "max_position_pct": 10.0,
                "vol_spike_threshold": 1.5,
                "max_loss_pct": 5.0,
            },
        },
        "sleeve_allocations": {"B": 0.2},
    }
    portfolio = MagicMock()
    portfolio.closed_trades = []
    portfolio.positions = {}
    portfolio.update_peak = MagicMock()
    portfolio.close_position = MagicMock(return_value=Decimal("0"))
    return {
        "config": cfg,
        "policy": policy,
        "wallet": MagicMock(),
        "cmc": MagicMock(),
        "pancake": MagicMock(),
        "bsc": MagicMock(),
        "portfolio": portfolio,
        "perps": MagicMock(),
        "ipfs": MagicMock(),
    }


class _AgentShim:
    def allow_trade(self, proposed):
        return True, "ok"
    def review_trade(self, *a, **kw):
        return True, "ok", "llm_disabled"


def _build_open_position(sleeve, sym: str, entry_px: float, entry_atr: float) -> Position:
    pos = Position(
        sleeve="B", symbol=sym, side="long",
        notional_usdc=Decimal("100"), risk_usdc=Decimal("2"),
        entry_ts=int(time.time()) - 60,
        entry_price=Decimal(str(entry_px)),
        stop_price=Decimal(str(entry_px - 2 * entry_atr)),
        tp_price=Decimal(str(entry_px * 1.03)),
    )
    sleeve.positions[sym] = pos
    sleeve.entry_atr[sym] = entry_atr
    return pos


@pytest.mark.asyncio
async def test_vol_spike_widens_stop():
    """Realized vol doubled → stop should be widened above the original."""
    s = SleeveBMomentum(name="B", components=_make_components(), agent=_AgentShim())
    entry_px = 100.0
    entry_atr = 2.0      # 2% ATR at entry → stop at 96.0
    pos = _build_open_position(s, "BTC", entry_px, entry_atr)
    original_stop = float(pos.stop_price)
    assert original_stop == pytest.approx(96.0)

    # current price: at 95.5 (just below original stop, NOT triggering yet)
    s.cmc.quotes_latest = AsyncMock(return_value={
        "data": {"BTC": {"quote": {"USD": {"price": 95.5}}}}
    })
    # current candles: ATR has doubled to 4.0
    candles = [{"high": 100 + i*0.5, "low": 100 - i*0.5, "close": 100 + i*0.1, "open": 100}
               for i in range(15)]
    s.cmc.ohlcv_historical = AsyncMock(return_value={
        "data": {"BTC": {"quotes": candles}}
    })

    # Pre-compute what _atr will return for the doubling
    computed_atr = s._atr(candles, 14)
    assert computed_atr > entry_atr * 1.5, f"test setup: ATR must spike; got {computed_atr}"

    # We don't expect an exit because the widened stop should be at or
    # below 95.5 (no fill).
    s.cmc.ohlcv_historical = AsyncMock(return_value={
        "data": {"BTC": {"quotes": candles}}
    })
    # Patch _atr to a known doubling
    s._atr = MagicMock(return_value=entry_atr * 2.0)  # 2x entry

    await s._monitor_open_positions(Decimal("1000"))

    # Stop should have widened DOWN (more room before exit on a long).
    new_stop = float(pos.stop_price)
    assert new_stop < original_stop, (
        f"vol spike must widen stop DOWN; was {original_stop}, now {new_stop}"
    )
    # And we should NOT have closed the position (price is at 95.5,
    # widened stop is at 95.0 — 95.5 is above it, no exit).
    assert "BTC" in s.positions, "position must NOT be closed by the rescale logic"


@pytest.mark.asyncio
async def test_vol_spike_clamps_to_max_loss_pct():
    """If the new stop would be too far below entry, clamp to max_loss_pct."""
    s = SleeveBMomentum(name="B", components=_make_components(), agent=_AgentShim())
    entry_px = 100.0
    entry_atr = 1.0      # tiny ATR at entry → stop at 98.0
    pos = _build_open_position(s, "BTC", entry_px, entry_atr)
    # max_loss_pct is 5.0 → floor stop at 95.0

    s.cmc.quotes_latest = AsyncMock(return_value={
        "data": {"BTC": {"quote": {"USD": {"price": 96.0}}}}
    })
    s.cmc.ohlcv_historical = AsyncMock(return_value={
        "data": {"BTC": {"quotes": [{"open": 100, "high": 105, "low": 95, "close": 100}] * 15}}
    })
    # Spike ATR to 8x → raw new stop = 100 - 2*8 = 84, but floor is 95.
    s._atr = MagicMock(return_value=8.0)

    await s._monitor_open_positions(Decimal("1000"))

    floor = entry_px * (1 - 0.05)   # 95.0
    assert float(pos.stop_price) >= floor, (
        f"rescale must not widen stop beyond max_loss_pct; "
        f"stop={pos.stop_price} floor={floor}"
    )


@pytest.mark.asyncio
async def test_no_rescale_when_vol_stable():
    """If ATR hasn't moved, the stop should be untouched."""
    s = SleeveBMomentum(name="B", components=_make_components(), agent=_AgentShim())
    entry_px = 100.0
    entry_atr = 2.0
    pos = _build_open_position(s, "BTC", entry_px, entry_atr)
    original_stop = float(pos.stop_price)

    s.cmc.quotes_latest = AsyncMock(return_value={
        "data": {"BTC": {"quote": {"USD": {"price": 100.5}}}}
    })
    s.cmc.ohlcv_historical = AsyncMock(return_value={
        "data": {"BTC": {"quotes": [{"open": 100, "high": 101, "low": 99, "close": 100}] * 15}}
    })
    s._atr = MagicMock(return_value=2.0)   # exactly entry, no spike

    await s._monitor_open_positions(Decimal("1000"))

    assert float(pos.stop_price) == original_stop
    assert "BTC" in s.positions
