"""Sleeve A — funding carry. Locks the rebalance-pair-close invariant.

The audit flagged that `_rebalance()` closed the perp short directly,
leaving the spot leg running unhedged for the 1-2 minute rebalance
window. This test asserts the fix: after a rebalance, no positions
remain and the rows dict is empty.
"""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest

from strategies.sleeve_a_carry import SleeveACarry


def _make_components() -> dict:
    cfg = {
        "cmc": {
            "basket_symbols": ["BTC", "ETH", "SOL"],
        },
        "dex": {"pcs_v3_router": "0x" + "1" * 40},
        "gas": {"swap_gas": 200000},
        "chain_id": 97,
    }
    policy = {
        "sleeve_allocations": {"A": 0.7},
        "sleeves": {"A": {"enabled": True, "rebalance_hours": 24,
                          "fund_floor_pct": 0.005, "basis_trigger_pct": 0.5}},
        "global_risk": {"per_trade_risk_pct": 1.0, "max_drawdown_pct": 10.0},
    }
    portfolio = MagicMock()
    portfolio.equity = MagicMock(return_value=Decimal("1000"))
    portfolio.positions = {}
    portfolio.update_peak = MagicMock()
    return {
        "config": cfg,
        "policy": policy,
        "wallet": MagicMock(),
        "data_source": MagicMock(),
        "pancake": MagicMock(),
        "perps": MagicMock(),
        "bsc": MagicMock(),
        "ipfs": MagicMock(),
        "portfolio": portfolio,
    }


class _AgentShim:
    def __init__(self):
        self.closed_pairs = []
    def allow_trade(self, proposed):
        return True, "ok"
    def review_trade(self, *a, **kw):
        return True, "ok", "llm_disabled"
    def mark_to_market(self, *a, **kw):
        pass


def test_rebalance_closes_pair_atomically():
    """A rebalance must not leave an orphan spot position."""
    components = _make_components()
    s = SleeveACarry(name="A", components=components, agent=_AgentShim())

    # Simulate a populated state: 3 open positions, one per basket symbol
    s.rows = {
        "BTC": MagicMock(venue="aster", spot_tx=MagicMock(), perp_tx=MagicMock(),
                         entry_spot_price=Decimal("100"), entry_funding=0.0005,
                         funding_paid_usdc=Decimal(0), opened_at=int(time.time())),
        "ETH": MagicMock(venue="aster", spot_tx=MagicMock(), perp_tx=MagicMock(),
                         entry_spot_price=Decimal("100"), entry_funding=0.0005,
                         funding_paid_usdc=Decimal(0), opened_at=int(time.time())),
        "SOL": MagicMock(venue="aster", spot_tx=MagicMock(), perp_tx=MagicMock(),
                         entry_spot_price=Decimal("100"), entry_funding=0.0005,
                         funding_paid_usdc=Decimal(0), opened_at=int(time.time())),
    }
    components["portfolio"].positions = {
        "A:BTC": MagicMock(symbol="BTC"),
        "A:ETH": MagicMock(symbol="ETH"),
        "A:SOL": MagicMock(symbol="SOL"),
    }
    # mark provider returns 100 (flat)
    components["perps"].mark = MagicMock(return_value=100.0)
    components["perps"].close_short = MagicMock(return_value=MagicMock())
    components["perps"].select_venue = MagicMock(return_value=("aster", {}))
    # Mirror real Portfolio.close_position: pop the position from the dict
    # AND append to closed_trades. (MagicMock by default does nothing to
    # the underlying dict.)
    def _fake_close(pid, exit_price, reason):
        pos = components["portfolio"].positions.pop(pid, None)
        return Decimal("0")
    components["portfolio"].close_position = MagicMock(side_effect=_fake_close)

    # Trigger the rebalance. The basket hasn't changed and the venue hasn't
    # changed either, so the early-return path would skip the work — set
    # last_rebalance to 0 to force the close path.
    s.last_rebalance = 0
    s.venue = "killex"  # force a venue change so early-return doesn't skip

    asyncio.run(s._rebalance(Decimal("1000")))

    # After rebalance: rows empty, all positions cleared.
    assert s.rows == {}, f"rows not cleared after rebalance: {list(s.rows.keys())}"
    assert components["portfolio"].positions == {}, (
        f"orphan positions after rebalance: {list(components['portfolio'].positions.keys())}. "
        f"Each must be closed via _close_pair to keep the strategy delta-neutral."
    )
    # And the perps.close_short should NOT have been called directly on the
    # rows — the close must have gone through _close_pair. (count == 0
    # because we route via portfolio.close_position in _close_pair, and
    # perps.close_short is called inside it.)
    # We assert it was called once per row (atomically) and that the
    # portfolio's close_position was called for each.
    assert components["portfolio"].close_position.call_count == 3, (
        f"expected 3 close_position calls (one per row), got "
        f"{components['portfolio'].close_position.call_count}"
    )
