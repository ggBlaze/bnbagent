"""Tests for v2.3.9: clamp_to_usdc_balance() must prevent STF reverts.

Background: on 2026-06-25, the bot broadcast a $1.00 USDC swap against
a wallet that only had $0.98 USDC. The swap reverted on-chain with
STF (Safe Transfer From) — burning 0.0007 BNB in gas with no effect.
The bot's internal state thought the position opened; on-chain reality
was a revert. This state divergence cost ~$3.71 in gas that day.

`clamp_to_usdc_balance()` clamps a USDC amount to the wallet's actual
on-chain balance, so the broadcast either uses an amount the wallet
can cover or is skipped entirely. Tested here:

- requested <= balance → no clamp, returns requested
- requested > balance → clamps to balance, sets was_clamped=True
- balance below min → returns 0, sets was_clamped=True (skip)
- edge cases: zero balance, zero requested, exact equality
"""
from __future__ import annotations

import pytest

from core.utils import clamp_to_usdc_balance


def test_no_clamp_when_requested_leq_balance():
    """If the wallet has enough USDC, return the requested amount."""
    requested = 10**18  # $1.00 (USDC has 18 decimals on BSC mainnet)
    balance = 5 * 10**18  # $5.00
    amount, was_clamped, bal = clamp_to_usdc_balance(requested, balance)
    assert amount == requested
    assert was_clamped is False
    assert bal == balance


def test_exact_equality_no_clamp():
    """Edge case: requested == balance. Must NOT clamp (boundary case)."""
    amount_in = 10**18
    balance = 10**18
    amount, was_clamped, _ = clamp_to_usdc_balance(amount_in, balance)
    assert amount == amount_in
    assert was_clamped is False


def test_clamps_when_requested_gt_balance():
    """If the wallet is short, clamp to balance."""
    requested = 10**18  # $1.00
    balance = 98 * 10**16  # $0.98 (this is the bug scenario)
    amount, was_clamped, _ = clamp_to_usdc_balance(requested, balance)
    assert amount == balance  # clamped down
    assert was_clamped is True


def test_returns_zero_when_balance_below_min():
    """If balance < min_amount_units, return 0 to signal 'skip this trade'."""
    requested = 10**18
    balance = 10**16  # $0.01
    min_amount = 5 * 10**16  # require at least $0.05
    amount, was_clamped, _ = clamp_to_usdc_balance(
        requested, balance, min_amount_units=min_amount,
    )
    assert amount == 0
    assert was_clamped is True


def test_zero_requested_no_clamp():
    """Zero requested is a no-op (caller bug, but don't break it)."""
    amount, was_clamped, _ = clamp_to_usdc_balance(0, 10**18)
    assert amount == 0
    assert was_clamped is False


def test_zero_balance_with_positive_requested():
    """Empty wallet + non-zero requested → clamped to 0."""
    amount, was_clamped, _ = clamp_to_usdc_balance(10**18, 0)
    assert amount == 0
    assert was_clamped is True


def test_returns_balance_unchanged():
    """The helper never mutates the balance arg (read-only contract)."""
    requested = 10**18
    balance = 5 * 10**17  # $0.50
    _, _, returned_balance = clamp_to_usdc_balance(requested, balance)
    assert returned_balance == balance


def test_realistic_stf_scenario():
    """The exact bug from 2026-06-25: $1 notional against $0.98 balance."""
    # Wallet has 0.9793499998018392 USDC (= the actual balance that day)
    balance = 979349999801839200  # ~$0.9793 in 18-decimal units
    requested = 10**18  # $1.00 — what the bot wanted
    amount, was_clamped, _ = clamp_to_usdc_balance(requested, balance)
    # The fix must clamp down to the balance, not broadcast $1 and revert
    assert amount == balance
    assert was_clamped is True
    # $0.50 minimum threshold: $0.9793 > $0.50, so this should still proceed
    # (if it had been below $0.50, the helper returns 0 instead)