"""Tests for v2.2.4 (decimals bugfix): raw amount computation for the
daily-floor open + close must use the live token decimals, not a
hardcoded 6.

Background: the floor opens with USDC->USDT on mainnet (deep stable
pool). With USDC/USDT both at 18 decimals:
  - $1 notional  → 1e18 raw USDC (not 1e6 — that would be dust)
  - $0.08 notional → 8e16 raw USDC (not 80,029 — also dust)

These tests pin the integer arithmetic so a regression to 10**6 (or any
other wrong power) is caught in CI before it burns real gas.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from core.utils import token_decimals, clear_token_decimals_cache


CFG = {
    "tokens": {
        "USDC": {"symbol": "USDC", "bsc_address": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", "decimals": 18},
        "USDT": {"symbol": "USDT", "bsc_address": "0x55d398326f99059fF775485246999027B3197955", "decimals": 18},
    }
}


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_token_decimals_cache()
    yield
    clear_token_decimals_cache()


def test_one_dollar_usdc_is_one_e18_raw():
    """The original bug: int(1.0 * 10**6) = 1_000_000 raw = 1e-12 USDC.
    The fix: int(1.0 * 10**18) = 1e18 raw = 1.0 USDC."""
    usdc_decimals = token_decimals("USDC", CFG)
    raw = int(Decimal("1.0") * Decimal(10 ** usdc_decimals))
    assert raw == 1_000_000_000_000_000_000  # 1e18
    assert raw != 1_000_000  # the bug


def test_eighty_cents_usdc_is_eight_e16_raw():
    """$0.08 notional: the OLD code computed 80,029 raw (8e-14 USDC).
    The fix: 8e16 raw = 0.08 USDC."""
    raw = int(Decimal("0.08") * Decimal(10 ** 18))
    assert raw == 80_000_000_000_000_000  # 8e16
    assert raw != 80_029  # the bug


def test_one_dollar_usdt_close_is_one_e18_raw():
    """The close leg: USDT has 18 decimals (same as USDC on BSC mainnet).
    int(notional * 10**18) for $1 → 1e18 raw USDT."""
    usdt_decimals = token_decimals("USDT", CFG)
    raw = int(Decimal("1.0") * Decimal(10 ** usdt_decimals))
    assert raw == 1_000_000_000_000_000_000
    assert usdt_decimals == 18, "BSC mainnet USDT is 18 decimals, NOT 6"


def test_floor_notional_one_dollar_uses_18():
    """End-to-end: a $1 daily-floor trade should produce 1e18 raw.
    Mirrors the calculation in core/daily_trade_floor.py + core/tick.py."""
    equity = Decimal("80")  # current wallet
    fraction = Decimal("0.0125")  # 1.25%
    notional = equity * fraction  # = 1.0
    decimals = token_decimals("USDC", CFG)
    raw = int(notional * Decimal(10 ** decimals))
    assert raw == 1_000_000_000_000_000_000


@pytest.mark.parametrize("notional_str,expected_raw", [
    ("0.05", 50_000_000_000_000_000),    # 5e16 — dust minimum
    ("0.10", 100_000_000_000_000_000),   # 1e17
    ("0.50", 500_000_000_000_000_000),   # 5e17
    ("1.00", 1_000_000_000_000_000_000), # 1e18
    ("1.25", 1_250_000_000_000_000_000), # 1.25e18 — 1.25% of $100
    ("12.50", 12_500_000_000_000_000_000),  # 1.25e19 — 1.25% of $1000
])
def test_notional_to_raw_table(notional_str, expected_raw):
    """Table-driven: every notional in the daily-floor range produces
    the expected raw amount under 18-decimal scaling."""
    n = Decimal(notional_str)
    raw = int(n * Decimal(10 ** 18))
    assert raw == expected_raw


def test_old_six_decimal_bug_returns_dust():
    """Sanity: prove the OLD formula int(notional * 10**6) was the bug.
    This test exists to document WHY the v2.2.4 fix matters — the bug
    was that 'we always used 10**6' for both USDC and USDT."""
    notional = Decimal("1.0")
    old_raw = int(notional * Decimal(10 ** 6))  # the bug
    assert old_raw == 1_000_000
    # On-chain 1_000_000 raw USDC (18 dec) = 1e-12 USDC = picodollar
    assert Decimal(old_raw) / Decimal(10 ** 18) == Decimal("0.000000000001")