"""Regression tests for the H-4 gas-price cap (v2.0.8).

H-4 was that sign_transaction hardcoded maxFeePerGas = 5 gwei, and
accepted any caller-supplied gasPrice. On BSC mainnet, gas spikes
(mints, liquidations, validator events) push the required fee to
10-20+ gwei. A tx signed at 5 gwei sits in the mempool indefinitely
or is replaced by an MEV bot.

Fix: sign_transaction accepts an OPTIONAL max_gas_price_gwei param.
If the resulting fee would exceed the cap, raise GasPriceTooHigh
BEFORE signing — so the sleeve can log gas_too_high_skip and move on.

These tests cover:
- default (no cap) → still signs at 5 gwei
- cap not exceeded → signs normally
- cap exceeded (gasPrice > cap) → raises
- cap exceeded (maxFeePerGas > cap) → raises
- the error type is the public GasPriceTooHigh exception
"""
import pytest
from web3 import Web3

from connectors.twak import TWAKWallet, GasPriceTooHigh
from tests.fixtures.wallets import AGENT_KEY


def _make_wallet():
    return TWAKWallet.from_private_key(AGENT_KEY)


def _tx_with_fee(fee_wei: int, *, gas_price: bool = True) -> dict:
    """Build a tx with an explicit gasPrice (or maxFeePerGas) in wei."""
    tx = {
        "to":       Web3.to_checksum_address("0x" + "d" * 40),
        "value":    10**18,
        "gas":      21000,
        "nonce":    0,
        "chainId":  56,
    }
    if gas_price:
        tx["gasPrice"] = fee_wei
    else:
        tx["maxFeePerGas"] = fee_wei
        tx["maxPriorityFeePerGas"] = fee_wei
    return tx


class TestGasCap:
    def test_no_cap_default_behavior_unchanged(self):
        """Without a cap, the wallet signs at 5 gwei default — unchanged."""
        w = _make_wallet()
        tx = {
            "to": Web3.to_checksum_address("0x" + "d" * 40),
            "value": 10**18, "gas": 21000, "nonce": 0, "chainId": 56,
        }
        signed = w.sign_transaction(tx, chain_id=56)
        assert signed.tx_hash.startswith("0x")

    def test_cap_not_exceeded_signs(self):
        """Cap present but below the fee → sign normally."""
        w = _make_wallet()
        tx = _tx_with_fee(3 * 10**9)   # 3 gwei
        signed = w.sign_transaction(tx, chain_id=56, max_gas_price_gwei=5.0)
        assert signed.tx_hash.startswith("0x")

    def test_cap_exceeded_gasPrice_raises(self):
        """Cap below the gasPrice → raise GasPriceTooHigh, do not sign."""
        w = _make_wallet()
        tx = _tx_with_fee(20 * 10**9)  # 20 gwei
        with pytest.raises(GasPriceTooHigh, match="20.00 gwei exceeds policy cap 5.00 gwei"):
            w.sign_transaction(tx, chain_id=56, max_gas_price_gwei=5.0)

    def test_cap_exceeded_maxFeePerGas_raises(self):
        """Cap below the maxFeePerGas → raise, do not sign."""
        w = _make_wallet()
        tx = _tx_with_fee(20 * 10**9, gas_price=False)
        with pytest.raises(GasPriceTooHigh, match="20.00 gwei exceeds policy cap"):
            w.sign_transaction(tx, chain_id=56, max_gas_price_gwei=10.0)

    def test_cap_at_exact_boundary_signs(self):
        """Cap exactly equal to the fee → sign (boundary inclusive)."""
        w = _make_wallet()
        tx = _tx_with_fee(5 * 10**9)   # 5 gwei
        signed = w.sign_transaction(tx, chain_id=56, max_gas_price_gwei=5.0)
        assert signed.tx_hash.startswith("0x")

    def test_cap_with_default_fee_5gwei(self):
        """Cap below the 5 gwei default → raise (catches the H-4 footgun)."""
        w = _make_wallet()
        tx = {
            "to": Web3.to_checksum_address("0x" + "d" * 40),
            "value": 10**18, "gas": 21000, "nonce": 0, "chainId": 56,
        }
        # no gasPrice / maxFeePerGas in tx → 5 gwei default
        with pytest.raises(GasPriceTooHigh, match="5.00 gwei exceeds policy cap 3.00 gwei"):
            w.sign_transaction(tx, chain_id=56, max_gas_price_gwei=3.0)

    def test_cap_none_means_no_cap(self):
        """max_gas_price_gwei=None explicitly disables the cap."""
        w = _make_wallet()
        tx = _tx_with_fee(100 * 10**9)   # 100 gwei, way above any sane cap
        signed = w.sign_transaction(tx, chain_id=56, max_gas_price_gwei=None)
        assert signed.tx_hash.startswith("0x")
