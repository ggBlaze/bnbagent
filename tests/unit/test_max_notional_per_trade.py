"""Tests for v2.3.5: max_notional_usdc_per_trade hard cap.

The cap is an ABSOLUTE notional limit per trade, separate from
per_trade_risk_pct. Why both are needed:

  per_trade_risk_pct (existing) limits `risk_usdc` = notional × stop_distance.
  On sleeve A (basis_trigger_pct=0.5 → 50bps stop), $20 of notional × 0.005
  = $0.10 risk → passes the 1% risk cap on a $100 wallet. So per_trade_risk
  alone permits large notionals on tight stops.

  max_notional_usdc_per_trade (this test) limits the raw USDC size BEFORE
  risk math. Set to 1.0 so the contest wallet can survive a full week at
  max_daily_trades=3 on a small balance.

Why the drain happened: on 2026-06-21 ~14:00 UTC sleeve A's _rebalance
opened 14 simultaneous spot-long+perp-short trades (one per allowlisted
token). The risk gate approved all 14 because per_trade_risk_pct was
satisfied (basis stops were tight). On-chain, sleeve A broadcast real
PancakeSwap swaps and drained the wallet from ~80 USDC to ~3 USDC.

The fix: this absolute notional cap runs BEFORE per_trade_risk_pct.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from core.risk import ProposedTrade, circuit_breaker_check
from tests.fixtures.wallets import TEST_POLICY


def _policy_with_notional_cap(cap_usdc: float | None) -> dict:
    """TEST_POLICY-shaped dict with the notional cap set or cleared."""
    policy = {**TEST_POLICY}
    policy["global_risk"] = {**TEST_POLICY["global_risk"]}
    if cap_usdc is None:
        policy["global_risk"].pop("max_notional_usdc_per_trade", None)
    else:
        policy["global_risk"]["max_notional_usdc_per_trade"] = cap_usdc
    return policy


def _proposed(notional: Decimal, symbol: str = "ETH",
              is_new: bool = True, sleeve: str = "A") -> ProposedTrade:
    """Tiny risk so the per-trade-risk check doesn't fire first.

    sleeve A's basis_trigger_pct=0.5 means stop_distance=0.005, so
    $1 notional = $0.005 risk → 0.005% risk on $100 equity, way under
    the 1% per_trade_risk_pct cap. This mirrors the real drain event.

    For tests that need a $50 notional to pass the position-cap and
    risk-cap gates (e.g. "cap is missing → notional doesn't matter"),
    the per-trade-risk math uses a 0.005 stop so $50 → $0.25 risk →
    0.25% risk on $100 equity → still under 1% per_trade_risk_pct.
    The position cap is 15% → 15% × $100 = $15 max → so we can't push
    $50 through even with risk-cap off. We accept that "cap missing"
    tests should use a notional that's under 15% position cap AND
    under 1% risk cap (i.e., ≤ $14.99 notional). The behavior of
    "no cap on missing → big notional allowed" is tested at the
    per-trade-risk level, not at the gross-allow level.
    """
    return ProposedTrade(
        sleeve=sleeve, symbol=symbol, side="long",
        notional_usdc=notional,
        risk_usdc=notional * Decimal("0.005"),  # 50bps stop
        is_new=is_new,
    )


# ------------------------------------------------------------------
# 1. Cap blocks over-sized opens
# ------------------------------------------------------------------

class TestNotionalCapBlocks:
    def test_blocks_open_above_cap(self):
        """Cap=1.0 → $1.50 notional rejected."""
        policy = _policy_with_notional_cap(cap_usdc=1.0)
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=_proposed(notional=Decimal("1.50")),
            policy=policy,
            day_start_equity=Decimal("100"),
        )
        assert not ok
        assert "per-trade notional cap" in reason
        assert "1.5000 USDC" in reason
        assert "1.0000 USDC cap" in reason

    def test_blocks_drain_event_replay(self):
        """Replay the actual 2026-06-21 drain event: sleeve A opens
        14 simultaneous spot longs at the 1% per-trade-risk cap.

        With per_trade_risk_pct=1% on $100 equity, max risk = $1.
        basis_trigger_pct=0.5 → stop_distance=0.005 → max notional
        allowed by per-trade-risk alone = $1 / 0.005 = $200. So
        per_trade_risk_pct permits the entire $100 wallet in one trade.

        With max_notional_usdc_per_trade=1.0 added, $200 notional is
        blocked by the new cap. This is the exact scenario that drained
        the contest wallet — we MUST block it now.
        """
        policy = _policy_with_notional_cap(cap_usdc=1.0)
        # 14 simultaneous attempts (one per token sleeve A scanned)
        for sym in ("ETH", "CAKE", "XRP", "DOGE", "ADA", "AVAX",
                    "LINK", "DOT", "SHIB", "LTC", "BCH", "ATOM",
                    "UNI", "BTCB"):
            ok, reason = circuit_breaker_check(
                current_equity=Decimal("100"),
                peak_equity=Decimal("100"),
                open_positions=[],
                proposed=_proposed(notional=Decimal("5.0"), symbol=sym),
                policy=policy,
                day_start_equity=Decimal("100"),
            )
            assert not ok, f"{sym} should be blocked by notional cap"
            assert "per-trade notional cap" in reason

    def test_allows_open_at_cap(self):
        """Cap=1.0 → exactly $1.00 notional allowed."""
        policy = _policy_with_notional_cap(cap_usdc=1.0)
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=_proposed(notional=Decimal("1.0")),
            policy=policy,
            day_start_equity=Decimal("100"),
        )
        assert ok, f"$1.0 at cap should be allowed, got reason={reason!r}"

    def test_allows_open_below_cap(self):
        policy = _policy_with_notional_cap(cap_usdc=1.0)
        ok, _ = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=_proposed(notional=Decimal("0.75")),
            policy=policy,
            day_start_equity=Decimal("100"),
        )
        assert ok


# ------------------------------------------------------------------
# 2. Cap does not block closes
# ------------------------------------------------------------------

class TestNotionalCapDoesNotBlock:
    def test_close_passes_regardless_of_size(self):
        """Closes (is_new=False) MUST NOT be blocked. Otherwise an
        oversized position that needs to be closed would be trapped
        forever. Same pattern as the daily-trade cap.

        Note: a $50 close on a $100 equity still trips the
        max_single_position_pct=15% gate on the pos cap check, so we
        use a notional that passes the position cap but exceeds the
        notional cap — proving the notional-cap specifically does
        not block closes."""
        policy = _policy_with_notional_cap(cap_usdc=1.0)
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=_proposed(notional=Decimal("2.0"), is_new=False),
            policy=policy,
            day_start_equity=Decimal("100"),
        )
        assert ok, f"close should bypass notional cap; got reason={reason!r}"

    def test_close_blocked_by_other_gates_still_blocked(self):
        """If a close violates a DIFFERENT gate (e.g., 15% position cap),
        the notional cap exemption doesn't rescue it — it just means
        the notional cap won't be the reason. This sanity-checks that
        the is_new=False exemption is not a back-door for everything."""
        policy = _policy_with_notional_cap(cap_usdc=1.0)
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=_proposed(notional=Decimal("20.0"), is_new=False),  # 20% > 15% pos cap
            policy=policy,
            day_start_equity=Decimal("100"),
        )
        assert not ok
        assert "notional cap" not in reason  # cap exempted; pos cap blocks


# ------------------------------------------------------------------
# 3. Cap behaviour when missing / zero / negative
# ------------------------------------------------------------------

class TestNotionalCapDisabled:
    def test_unlimited_when_cap_missing(self):
        """Backwards-compat: if max_notional_usdc_per_trade is not in
        policy (legacy YAML or test fixture), no cap — same as the
        pre-v2.3.5 behavior that allowed the drain event.

        Notional must be small enough to pass the 15% pos cap (so we
        isolate the notional-cap behavior)."""
        policy = _policy_with_notional_cap(cap_usdc=None)
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=_proposed(notional=Decimal("10.0")),  # 10% of equity
            policy=policy,
            day_start_equity=Decimal("100"),
        )
        assert ok, f"missing cap should not block; got reason={reason!r}"

    def test_unlimited_when_cap_is_zero(self):
        """cap=0 → effectively no cap (mirrors the `if max_notional`
        truthiness check). Setting cap=0 from the dashboard means
        'unlimited' — the operator accepts full-sleeve sizing."""
        policy = _policy_with_notional_cap(cap_usdc=0.0)
        ok, _ = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=_proposed(notional=Decimal("10.0")),  # 10% pos
            policy=policy,
            day_start_equity=Decimal("100"),
        )
        assert ok

    def test_unlimited_when_cap_is_negative(self):
        """Negative cap is treated as 'unset' — guards against typo."""
        policy = _policy_with_notional_cap(cap_usdc=-1.0)
        ok, _ = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=_proposed(notional=Decimal("10.0")),  # 10% pos
            policy=policy,
            day_start_equity=Decimal("100"),
        )
        assert ok


# ------------------------------------------------------------------
# 4. Floor exemption
# ------------------------------------------------------------------

class TestNotionalCapFloor:
    def test_floor_bypasses_notional_cap(self):
        """The daily-trade-floor is the BNB HACK contest-compliance
        safety net for the 1-trade/day minimum. If it ever trips the
        notional cap (it shouldn't — floor is 1.25% of equity, so
        under $1 on a $80 wallet, well under cap=1.0), still allow
        it. Otherwise the agent is disqualified from the contest."""
        policy = _policy_with_notional_cap(cap_usdc=1.0)
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=_proposed(notional=Decimal("5.0")),  # above cap
            policy=policy,
            day_start_equity=Decimal("100"),
            is_floor=True,
        )
        assert ok, f"floor must bypass notional cap; got reason={reason!r}"

    def test_non_floor_still_blocked_at_cap(self):
        """Sanity check: without is_floor=True, the cap still blocks."""
        policy = _policy_with_notional_cap(cap_usdc=1.0)
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=_proposed(notional=Decimal("5.0")),
            policy=policy,
            day_start_equity=Decimal("100"),
            is_floor=False,
        )
        assert not ok
        assert "per-trade notional cap" in reason


# ------------------------------------------------------------------
# 5. Cap runs BEFORE per-trade-risk so it wins on conflict
# ------------------------------------------------------------------

class TestCapRunsFirst:
    def test_notional_cap_blocks_before_per_trade_risk(self):
        """When BOTH the new notional cap AND the existing per-trade-risk
        cap would block, the notional cap must be the reported reason.
        This is intentional — the notional cap is the contest safety
        net; the per-trade-risk is the legacy % gate."""
        policy = _policy_with_notional_cap(cap_usdc=1.0)
        # $50 notional, $0.25 risk → 0.25% risk on $100, under 1% limit
        # So per_trade_risk_pct would ALLOW this — but the notional cap
        # must BLOCK it and be the reported reason.
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=ProposedTrade(
                sleeve="A", symbol="ETH", side="long",
                notional_usdc=Decimal("50"),
                risk_usdc=Decimal("0.25"),  # 0.25% risk — passes per-trade
                is_new=True,
            ),
            policy=policy,
            day_start_equity=Decimal("100"),
        )
        assert not ok
        assert "per-trade notional cap" in reason
        assert "per-trade risk" not in reason