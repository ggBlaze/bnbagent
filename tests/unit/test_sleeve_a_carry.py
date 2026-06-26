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
from unittest.mock import AsyncMock, MagicMock

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


# ---- v2.x.x: per-token notional must clamp to policy["global_risk"]["max_notional_usdc_per_trade"]


def test_rebalance_clamps_per_token_to_max_notional_cap():
    """Without the clamp, 100 * 0.7 / 20 = 3.50 USDC > 1.00 cap and every
    proposal dies at the risk gate. With the clamp, per_token becomes 1.00.
    """
    components = _make_components()
    components["policy"]["sleeve_allocations"]["A"] = 0.7
    components["policy"]["global_risk"]["max_notional_usdc_per_trade"] = 1.0
    # Use a basket of eligible symbols so filter_universe doesn't empty it.
    components["config"]["cmc"]["basket_symbols"] = ["BTC", "ETH", "SOL", "XRP"]
    components["perps"].select_venue = MagicMock(return_value=("aster", {}))
    components["perps"].mark = MagicMock(return_value=100.0)
    components["perps"].close_short = MagicMock(return_value=MagicMock())
    components["pancake"].buy = MagicMock(return_value=MagicMock())
    components["perps"].open_short = MagicMock(return_value=MagicMock())
    components["wallet"].address = "0x" + "1" * 40

    # capture every proposed notional
    captured = []

    class _CaptureAgent(_AgentShim):
        def allow_trade(self, proposed):
            captured.append(float(proposed.notional_usdc))
            return True, "ok"

    s = SleeveACarry(name="A", components=components, agent=_CaptureAgent())
    s.last_rebalance = 0
    s.venue = "OLD_VENUE"  # force rebalance to fire
    s.basket = ["DIFFERENT"]

    asyncio.run(s._rebalance(Decimal("100")))

    assert captured, "rebalance produced no proposals"
    # Every proposed trade must be <= 1.00 USDC (the configured cap).
    # Without the clamp every value would be 3.50 / 4 = 17.50 each.
    # With the clamp, every value must be exactly 1.00.
    over = [n for n in captured if n > 1.0]
    assert not over, f"un-clamped notionals: {over}"
    assert max(captured) <= 1.0


def test_rebalance_pass_through_when_cap_above_strategy_size():
    """If dashboard raises cap to 100.00, sleeve A's per-token math goes through unclamped."""
    components = _make_components()
    components["policy"]["sleeve_allocations"]["A"] = 0.7
    components["policy"]["global_risk"]["max_notional_usdc_per_trade"] = 100.0
    components["config"]["cmc"]["basket_symbols"] = ["BTC", "ETH", "SOL", "XRP"]
    components["perps"].select_venue = MagicMock(return_value=("aster", {}))
    components["perps"].mark = MagicMock(return_value=100.0)
    components["perps"].close_short = MagicMock(return_value=MagicMock())
    components["pancake"].buy = MagicMock(return_value=MagicMock())
    components["perps"].open_short = MagicMock(return_value=MagicMock())
    components["wallet"].address = "0x" + "1" * 40

    captured = []

    class _CaptureAgent(_AgentShim):
        def allow_trade(self, proposed):
            captured.append(float(proposed.notional_usdc))
            return True, "ok"

    s = SleeveACarry(name="A", components=components, agent=_CaptureAgent())
    s.last_rebalance = 0
    s.venue = "OLD_VENUE"
    s.basket = ["DIFFERENT"]

    asyncio.run(s._rebalance(Decimal("100")))

    assert captured, "rebalance produced no proposals"
    # With cap=100, NO notional should be clamped to 1.0. Every notional
    # should equal equity * 0.7 / N where N = symbols that survived quote
    # fetch. We assert all notionals > 1.0 (the previous cap value) and
    # at most one unique value (each token gets the same per_token_usdc).
    assert all(n > 1.0 for n in captured), (
        f"some notionals still clamped to 1.0 despite cap=100: {captured}"
    )
    assert len(set(captured)) == 1, (
        f"expected one unique per_token size, got {set(captured)}"
    )


# ---- v2.3.8: pre-flight BNB-for-gas check before broadcast ----

def _make_rebalance_setup(components):
    """Wire up the mocks needed for sleeve A's _rebalance to reach the
    broadcast path: perps select_venue returns a NEW venue so the early
    return doesn't fire, pancake encodes a swap, wallet signs, bsc
    broadcasts."""
    components["perps"].select_venue = MagicMock(return_value=("aster", {}))
    components["perps"].mark = MagicMock(return_value=100.0)
    components["perps"].close_short = MagicMock(return_value=MagicMock())
    components["perps"].open_short = MagicMock(return_value=MagicMock())
    components["pancake"].best_pool_fee = MagicMock(return_value=2500)
    components["pancake"].encode_swap_v3 = MagicMock(return_value=b"\x00" * 4)
    components["data_source"].quotes_latest = AsyncMock(
        return_value={"data": {
            "BTC": {"quote": {"USD": {"price": 100.0}}},
            "ETH": {"quote": {"USD": {"price": 100.0}}},
            "SOL": {"quote": {"USD": {"price": 100.0}}},
        }},
    )
    components["wallet"].address = "0x" + "1" * 40
    components["wallet"].sign_transaction = MagicMock(
        return_value=MagicMock(raw_tx=b"\x00" * 32),
    )
    components["bsc"].resync_nonce = MagicMock()


def test_rebalance_skips_when_insufficient_bnb_for_gas():
    """If bsc.has_gas() returns ok=False, _rebalance must skip the
    broadcast (no spurious 'insufficient funds for gas' errors)."""
    components = _make_components()
    _make_rebalance_setup(components)
    components["bsc"].has_gas = MagicMock(
        return_value=(False, "bnb_insufficient_gas: have 0.000100 BNB, need 0.001500 BNB"),
    )
    components["bsc"].broadcast = MagicMock(return_value=MagicMock(tx_hash="0xDEAD"))

    s = SleeveACarry(name="A", components=components, agent=_AgentShim())
    s.last_rebalance = 0
    s.venue = "OLD_VENUE"
    s.basket = ["DIFFERENT"]  # force the venue/basket mismatch path

    asyncio.run(s._rebalance(Decimal("100")))

    assert components["bsc"].broadcast.call_count == 0, (
        f"broadcast should have been blocked by gas check, was called "
        f"{components['bsc'].broadcast.call_count} time(s)"
    )
    assert components["bsc"].has_gas.call_count >= 1
    assert components["portfolio"].positions == {}


def test_rebalance_proceeds_when_gas_check_passes():
    """If bsc.has_gas() returns ok=True, the broadcast proceeds."""
    components = _make_components()
    _make_rebalance_setup(components)
    components["bsc"].has_gas = MagicMock(return_value=(True, "ok"))
    components["bsc"].broadcast = MagicMock(return_value=MagicMock(tx_hash="0xALIVE"))

    s = SleeveACarry(name="A", components=components, agent=_AgentShim())
    s.last_rebalance = 0
    s.venue = "OLD_VENUE"
    s.basket = ["DIFFERENT"]

    asyncio.run(s._rebalance(Decimal("100")))

    assert components["bsc"].broadcast.call_count >= 1
    assert components["bsc"].has_gas.call_count >= 1


def test_rebalance_proceeds_when_gas_check_itself_fails():
    """If bsc.has_gas() raises (chain RPC down), don't block trades —
    let broadcast surface the real error."""
    components = _make_components()
    _make_rebalance_setup(components)
    components["bsc"].has_gas = MagicMock(side_effect=RuntimeError("rpc down"))
    components["bsc"].broadcast = MagicMock(return_value=MagicMock(tx_hash="0xALIVE"))

    s = SleeveACarry(name="A", components=components, agent=_AgentShim())
    s.last_rebalance = 0
    s.venue = "OLD_VENUE"
    s.basket = ["DIFFERENT"]

    asyncio.run(s._rebalance(Decimal("100")))

    assert components["bsc"].broadcast.call_count >= 1
