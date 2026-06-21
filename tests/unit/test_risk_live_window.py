"""v2.1.8 (live-window): the risk engine must hard-block new orders
before the BNB HACK live window opens (and after it closes), so a
missed kill switch can't accidentally pre-trade."""
from __future__ import annotations
from copy import deepcopy
from decimal import Decimal

from core.risk import circuit_breaker_check, ProposedTrade
from tests.fixtures.wallets import TEST_POLICY


def _policy_with_window(start: str | None = None, end: str | None = None) -> dict:
    p = deepcopy(TEST_POLICY)
    if start is not None:
        p["global_risk"]["live_window_start"] = start
    if end is not None:
        p["global_risk"]["live_window_end"] = end
    return p


def _proposed() -> ProposedTrade:
    return ProposedTrade("B", "ETH", "long", Decimal("5"), Decimal("0.5"))


class TestLiveWindow:
    def test_no_window_set_means_no_gate(self):
        """If live_window_start is absent, trades pass (backward compat)."""
        policy = _policy_with_window(start=None, end=None)
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=_proposed(),
            policy=policy,
            day_start_equity=Decimal("100"),
        )
        assert ok, f"expected pass without live window, got: {reason}"

    def test_blocks_trade_before_live_window(self):
        """Pre-window trades must be blocked even if all other checks pass."""
        policy = _policy_with_window(
            start="2026-06-22T12:00:00Z",
            end="2026-06-28T12:00:00Z",
        )
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=_proposed(),
            policy=policy,
            day_start_equity=Decimal("100"),
            now_ts=1_782_024_780,  # 2026-06-21 06:53 UTC — before window
        )
        assert not ok
        assert "before live window" in reason

    def test_allows_trade_inside_live_window(self):
        """Trades inside the window are allowed (other checks pass)."""
        policy = _policy_with_window(
            start="2026-06-22T12:00:00Z",
            end="2026-06-28T12:00:00Z",
        )
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=_proposed(),
            policy=policy,
            day_start_equity=Decimal("100"),
            now_ts=1_782_388_800,  # 2026-06-25 12:00 UTC — inside window
        )
        assert ok, f"expected pass inside live window, got: {reason}"

    def test_blocks_trade_after_live_window_closes(self):
        """Post-window trades must be blocked so the agent doesn't keep
        running once the BNB HACK scoring period ends."""
        policy = _policy_with_window(
            start="2026-06-22T12:00:00Z",
            end="2026-06-28T12:00:00Z",
        )
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=_proposed(),
            policy=policy,
            day_start_equity=Decimal("100"),
            now_ts=1_782_651_600,  # 2026-06-28 13:00 UTC — after window
        )
        assert not ok
        assert "after live window" in reason

    def test_invalid_iso_string_does_not_crash(self):
        """A malformed live_window_start should not crash the engine —
        the gate just doesn't apply. Surface a warning."""
        policy = _policy_with_window(start="not-a-date")
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=_proposed(),
            policy=policy,
            day_start_equity=Decimal("100"),
        )
        assert ok, f"malformed window should fall through, got: {reason}"

    def test_kill_switch_still_works_alongside_live_window(self):
        """The kill switch is the more-authoritative gate. Even during
        the live window, an engaged kill switch blocks trades."""
        policy = _policy_with_window(
            start="2026-06-22T12:00:00Z",
            end="2026-06-28T12:00:00Z",
        )
        policy["_kill_switch"] = True
        policy["_kill_reason"] = "manual test"
        ok, reason = circuit_breaker_check(
            current_equity=Decimal("100"),
            peak_equity=Decimal("100"),
            open_positions=[],
            proposed=_proposed(),
            policy=policy,
            day_start_equity=Decimal("100"),
            now_ts=1_782_350_400,  # inside window
        )
        assert not ok
        assert "kill switch" in reason
