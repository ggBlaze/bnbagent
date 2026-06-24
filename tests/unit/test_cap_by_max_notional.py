"""Tests for v2.x.x: cap_by_max_notional helper + per-strategy clamp.

The helper clamps a strategy's proposed size to the absolute per-trade
USDC limit in `policy.global_risk.max_notional_usdc_per_trade`. Without
it, all three sleeves compute a size from Kelly / basket-split math
that exceeds the cap (3.50, 6.25, 2.50 USDC respectively) and every
proposal gets rejected at the risk gate with
"per-trade notional cap: X USDC > 1.0000 USDC cap".

The clamp reads from policy so the dashboard's `cfg-notional` form
field (line 3086 of dashboard/frontend/index.html) flows through
without code changes.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from core.risk import cap_by_max_notional


def _policy_with_cap(cap):
    return {"global_risk": {"max_notional_usdc_per_trade": cap}}


def test_clamps_size_above_cap_down_to_cap():
    """The whole point: a 3.50 USDC proposal becomes 1.00 USDC."""
    out = cap_by_max_notional(Decimal("3.5"), _policy_with_cap(1.0))
    assert out == Decimal("1.0")


def test_passes_through_size_at_or_below_cap():
    """If strategy math already sized below the cap, leave it alone."""
    out = cap_by_max_notional(Decimal("0.75"), _policy_with_cap(1.0))
    assert out == Decimal("0.75")


def test_passes_through_when_cap_missing():
    """Legacy / opt-out: missing key means no absolute cap."""
    out = cap_by_max_notional(Decimal("5.0"), {"global_risk": {}})
    assert out == Decimal("5.0")


def test_passes_through_when_cap_zero():
    """Zero means disabled (matches allow_trade check on max_notional > 0)."""
    out = cap_by_max_notional(Decimal("5.0"), _policy_with_cap(0))
    assert out == Decimal("5.0")


def test_passes_through_when_cap_none():
    out = cap_by_max_notional(Decimal("5.0"), _policy_with_cap(None))
    assert out == Decimal("5.0")


def test_higher_cap_passes_through():
    """If dashboard moves cap to 5.00, the 3.50 USDC proposal goes through."""
    out = cap_by_max_notional(Decimal("3.5"), _policy_with_cap(5.0))
    assert out == Decimal("3.5")


def test_decimal_string_cap_is_handled():
    """Policy.yaml might give '1.0' as a string — helper must not crash."""
    out = cap_by_max_notional(Decimal("3.5"), _policy_with_cap("1.0"))
    assert out == Decimal("1.0")


def test_helper_is_pure():
    """No mutation of input size or policy."""
    size = Decimal("3.5")
    policy = _policy_with_cap(1.0)
    _ = cap_by_max_notional(size, policy)
    assert size == Decimal("3.5")
    assert policy == _policy_with_cap(1.0)
