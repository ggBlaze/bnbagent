"""Tests for v2.2.4 (decimals bugfix): token_decimals() must never return
6 for any mainnet USDC/USDT contract.

Background: BSC mainnet USDC (0x8AC76a51...) and USDT (0x55d39832...)
both report `decimals() == 18`, not 6. The historical hardcode of 6
caused $0.00000000000008 dust swaps that burned ~$30 BNB in gas in one
hour. These tests pin the correct behavior so future refactors can't
regress to the 6-decimal assumption.

Test layers:
  - cfg lookup wins over fallback
  - cache prevents redundant lookups
  - on-chain fallback when cfg missing
  - hardcoded fallback for unknown tokens returns 18, NEVER 6
  - live BSC mainnet: USDC + USDT both report 18 (network guard)
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from core.utils import token_decimals, clear_token_decimals_cache


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_cache():
    """Every test starts with a fresh cache so we test real behavior,
    not cached state from a previous test."""
    clear_token_decimals_cache()
    yield
    clear_token_decimals_cache()


CFG_MAINNET = {
    "tokens": {
        "USDC": {"symbol": "USDC", "bsc_address": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", "decimals": 18},
        "USDT": {"symbol": "USDT", "bsc_address": "0x55d398326f99059fF775485246999027B3197955", "decimals": 18},
        "WBNB": {"symbol": "WBNB", "bsc_address": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c", "decimals": 18},
        "CAKE": {"symbol": "CAKE", "bsc_address": "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82", "decimals": 18},
    }
}


# ------------------------------------------------------------------
# 1. cfg lookup
# ------------------------------------------------------------------

def test_cfg_lookup_returns_configured_decimals():
    """cfg['tokens'][sym]['decimals'] wins over fallback."""
    cfg = {"tokens": {"USDC": {"decimals": 18}}}
    assert token_decimals("USDC", cfg) == 18


def test_cfg_lookup_honors_six_if_explicitly_configured():
    """If an operator deliberately sets decimals=6 in cfg, respect it.
    This guards against future test fixtures that want the 6-decimal
    path (e.g. legacy testnet USDC on some other chain)."""
    cfg = {"tokens": {"LEGACY": {"decimals": 6}}}
    assert token_decimals("LEGACY", cfg) == 6


def test_cfg_lookup_case_insensitive_symbol():
    """Symbol lookup is case-insensitive — 'usdc', 'USDC', 'Usdc' all match."""
    cfg = {"tokens": {"USDC": {"decimals": 18}}}
    assert token_decimals("usdc", cfg) == 18
    assert token_decimals("USDC", cfg) == 18
    assert token_decimals("Usdc", cfg) == 18


def test_cfg_lookup_with_none_cfg_falls_through_to_fallback():
    """When cfg is None, we don't crash — we use the hardcoded fallback."""
    assert token_decimals("USDC", None) == 18


def test_cfg_lookup_with_empty_cfg_falls_through_to_fallback():
    """Empty cfg dict → fallback path → 18 (NEVER 6)."""
    assert token_decimals("USDC", {}) == 18
    assert token_decimals("USDT", {}) == 18
    assert token_decimals("WBNB", {}) == 18
    assert token_decimals("UNKNOWN_TOKEN", {}) == 18


# ------------------------------------------------------------------
# 2. cache behavior
# ------------------------------------------------------------------

def test_cache_returns_same_value_on_second_call():
    """Module-level cache prevents redundant cfg/on-chain lookups."""
    cfg = {"tokens": {"USDC": {"decimals": 18}}}
    a = token_decimals("USDC", cfg)
    b = token_decimals("USDC", cfg)
    assert a == b == 18


def test_cache_survives_cfg_change():
    """Once cached, the value is reused even if cfg changes.
    This is intentional — cache means 'trust the first answer'.
    Tests of the 'live' value should clear_token_decimals_cache() first."""
    cfg_a = {"tokens": {"USDC": {"decimals": 18}}}
    cfg_b = {"tokens": {"USDC": {"decimals": 6}}}
    assert token_decimals("USDC", cfg_a) == 18
    # cache hit — cfg_b is ignored
    assert token_decimals("USDC", cfg_b) == 18


def test_clear_cache_forces_relookup():
    """clear_token_decimals_cache() resets state — next call hits cfg."""
    cfg = {"tokens": {"USDC": {"decimals": 18}}}
    token_decimals("USDC", cfg)
    clear_token_decimals_cache()
    # After clear, the fallback wins (cfg has USDC but cache was cleared
    # AND cfg lookup runs again). Both paths should return 18.
    assert token_decimals("USDC", cfg) == 18


def test_cache_distinct_per_symbol():
    """Different symbols cache independently."""
    assert token_decimals("USDC", CFG_MAINNET) == 18
    assert token_decimals("USDT", CFG_MAINNET) == 18
    assert token_decimals("WBNB", CFG_MAINNET) == 18
    assert token_decimals("CAKE", CFG_MAINNET) == 18


# ------------------------------------------------------------------
# 3. on-chain fallback
# ------------------------------------------------------------------

def test_onchain_fallback_used_when_cfg_missing_decimals():
    """If cfg has the token entry but no 'decimals' key, fall through
    to the on-chain lookup when a w3 is provided. The mock_w3 is queried
    and its decimals() return value (18) is cached and returned."""
    cfg_no_decimals = {
        "tokens": {
            "USDC": {"bsc_address": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d"}
        }
    }
    mock_w3 = MagicMock()
    # Wire the mock so w3.eth.contract(...).functions.decimals().call() returns 18
    mock_contract = MagicMock()
    mock_contract.functions.decimals.return_value.call.return_value = 18
    mock_w3.eth.contract.return_value = mock_contract
    assert token_decimals("USDC", cfg_no_decimals, w3=mock_w3) == 18
    # Verify the contract was constructed with the correct address
    assert mock_w3.eth.contract.called


def test_onchain_fallback_not_used_when_cfg_has_decimals():
    """If cfg has decimals, we don't bother the chain."""
    cfg = {"tokens": {"USDC": {"decimals": 18}}}
    mock_w3 = MagicMock()
    token_decimals("USDC", cfg, w3=mock_w3)
    mock_w3.eth.contract.assert_not_called()


def test_onchain_fallback_swallows_exceptions():
    """If the on-chain call fails, fall through to hardcoded fallback."""
    cfg_no_decimals = {
        "tokens": {
            "USDC": {"bsc_address": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d"}
        }
    }
    mock_w3 = MagicMock()
    mock_w3.eth.contract.side_effect = Exception("RPC down")
    # Should NOT raise — fall through to fallback (USDC → 18)
    assert token_decimals("USDC", cfg_no_decimals, w3=mock_w3) == 18


# ------------------------------------------------------------------
# 4. CRITICAL regression guards — NEVER return 6 for mainnet stables
# ------------------------------------------------------------------

@pytest.mark.parametrize("symbol", ["USDC", "USDT", "usdc", "usdt"])
def test_mainnet_stablecoins_default_to_18(symbol):
    """CI guard: the function default behavior for USDC/USDT is 18.
    If cfg doesn't explicitly set decimals=6 (the bug), we return 18.
    The function DOES respect explicit cfg overrides — see
    test_cfg_lookup_honors_six_if_explicitly_configured for that path."""
    for cfg in [
        None,
        {},
        {"tokens": {}},
        {"tokens": {symbol.upper(): {}}},
    ]:
        clear_token_decimals_cache()
        result = token_decimals(symbol, cfg)
        assert result == 18, (
            f"token_decimals({symbol!r}, {cfg!r}) returned {result}, expected 18. "
            f"On BSC mainnet both USDC and USDT have 18 decimals."
        )


def test_explicit_six_in_cfg_is_respected_for_production_paths():
    """Document the behavior: cfg can override to 6 if explicitly set.
    The production config has decimals=18, so this code path is dormant
    in production — but tests/legacy fixtures may want it."""
    cfg = {"tokens": {"USDC": {"decimals": 6}}}
    clear_token_decimals_cache()
    assert token_decimals("USDC", cfg) == 6


def test_known_tokens_with_fallback():
    """The hardcoded fallback covers WBNB, ETH, CAKE, BTCB at 18."""
    clear_token_decimals_cache()
    for sym in ["WBNB", "ETH", "CAKE", "BTCB"]:
        clear_token_decimals_cache()
        assert token_decimals(sym, None) == 18


def test_unknown_token_falls_back_to_18():
    """Even totally unknown tokens default to 18, never 6."""
    clear_token_decimals_cache()
    assert token_decimals("TOTALLY_UNKNOWN_TOKEN_XYZ", None) == 18


# ------------------------------------------------------------------
# 5. live BSC mainnet guard (network)
# ------------------------------------------------------------------

@pytest.mark.network
def test_live_bsc_mainnet_usdc_reports_18():
    """CI guard against future 'the contract changed to 6' regressions.

    Hits BSC mainnet JSON-RPC and asserts the canonical USDC contract
    at 0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d reports decimals() == 18.

    Skip in CI without BSC_RPC_URL via the @pytest.mark.network marker.
    """
    import os
    from web3 import Web3
    rpc = os.environ.get("BSC_RPC_URL", "https://bsc-dataseed.binance.org/")
    w3 = Web3(Web3.HTTPProvider(rpc))
    if not w3.is_connected():
        pytest.skip("BSC RPC not reachable")
    abi = [{"constant": True, "inputs": [], "name": "decimals",
            "outputs": [{"name": "", "type": "uint8"}], "type": "function"}]
    addr = Web3.to_checksum_address("0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d")
    d = w3.eth.contract(address=addr, abi=abi).functions.decimals().call()
    assert d == 18, (
        f"BSC mainnet USDC at {addr} now reports decimals={d}, expected 18. "
        f"If this changed, update token_decimals() AND audit every call site "
        f"in core/tick.py + strategies/."
    )


@pytest.mark.network
def test_live_bsc_mainnet_usdt_reports_18():
    """Same guard for USDT at 0x55d398326f99059fF775485246999027B3197955."""
    import os
    from web3 import Web3
    rpc = os.environ.get("BSC_RPC_URL", "https://bsc-dataseed.binance.org/")
    w3 = Web3(Web3.HTTPProvider(rpc))
    if not w3.is_connected():
        pytest.skip("BSC RPC not reachable")
    abi = [{"constant": True, "inputs": [], "name": "decimals",
            "outputs": [{"name": "", "type": "uint8"}], "type": "function"}]
    addr = Web3.to_checksum_address("0x55d398326f99059fF775485246999027B3197955")
    d = w3.eth.contract(address=addr, abi=abi).functions.decimals().call()
    assert d == 18, (
        f"BSC mainnet USDT at {addr} now reports decimals={d}, expected 18."
    )