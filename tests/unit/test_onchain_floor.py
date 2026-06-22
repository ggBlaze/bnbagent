"""Tests for v2.2.0 (onchain-floor): real on-chain USDC->WBNB swap
via PancakeSwap V3 when the daily floor fires on mainnet.

The Agent's submit_floor_trade() must:
  - attempt a real on-chain swap when on mainnet with bsc/pancake/wallet
  - fall back to the paper path on testnet/replay/mock
  - fall back to the paper path on any on-chain error
  - record the tx_hash + BscTrace URL on success

These tests use MagicMock for bsc/pancake/wallet to keep them
network-free.
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

import pytest

from core.tick import Agent
from core.portfolio import Portfolio
from core.risk import ProposedTrade


# ------------------------------------------------------------------
# _can_do_onchain gating
# ------------------------------------------------------------------

def test_can_do_onchain_true_when_mainnet_and_components():
    """v2.2.0: with mode=mainnet + bsc/pancake/wallet set, the agent
    should attempt a real on-chain swap."""
    policy = {"global_risk": {"live_window_start": "2026-06-22T12:00:00+00:00"}}
    pf = Portfolio(starting_equity=Decimal("100"))
    components = {
        "bsc": MagicMock(),
        "pancake": MagicMock(),
        "wallet": MagicMock(),
        "config": {"mode": "mainnet"},
    }
    agent = Agent(policy, pf, components=components)
    assert agent._can_do_onchain() is True


def test_can_do_onchain_false_on_testnet():
    """v2.2.0: on testnet the floor must NOT attempt on-chain.
    No real network, no real money, just paper sim."""
    policy = {"global_risk": {}}
    pf = Portfolio(starting_equity=Decimal("100"))
    components = {
        "bsc": MagicMock(),
        "pancake": MagicMock(),
        "wallet": MagicMock(),
        "config": {"mode": "testnet"},
    }
    agent = Agent(policy, pf, components=components)
    assert agent._can_do_onchain() is False


def test_can_do_onchain_false_when_components_missing():
    """v2.2.0: if bsc/pancake/wallet are missing, the floor must
    fall back to paper even on mainnet."""
    policy = {"global_risk": {}}
    pf = Portfolio(starting_equity=Decimal("100"))
    components = {"config": {"mode": "mainnet"}}  # no bsc/pancake/wallet
    agent = Agent(policy, pf, components=components)
    assert agent._can_do_onchain() is False


# ------------------------------------------------------------------
# _ensure_usdc_approval
# ------------------------------------------------------------------

def test_usdc_approval_skipped_when_already_approved():
    """v2.2.0: if the existing allowance is enough, no approval tx
    is sent (saves gas)."""
    policy = {"global_risk": {}}
    pf = Portfolio(starting_equity=Decimal("100"))

    fake_bsc = MagicMock()
    fake_w3 = MagicMock()
    fake_erc20 = MagicMock()
    fake_erc20.functions.allowance.return_value.call.return_value = 10**18  # 1 USDC enough
    fake_w3.eth.contract.return_value = fake_erc20
    fake_bsc.w3.return_value = fake_w3
    fake_wallet = MagicMock()
    fake_wallet.address = "0xed669AE6632be9440cdACBE5ac5181D5BC871CC9"

    components = {
        "bsc": fake_bsc,
        "wallet": fake_wallet,
        "config": {
            "mode": "mainnet",
            "chain_id": 56,
            "dex": {"pcs_v3_router": "0x13f4EA83D0bd40E75C8222255bc855a974568Dd4"},
            "gas": {"max_gwei": 5},
        },
    }
    agent = Agent(policy, pf, components=components)
    # Asking for approval of 80000 (6-dec USDC = $0.08), allowance is way higher
    result = agent._ensure_usdc_approval(80_000)
    assert result is None
    # broadcast was NOT called
    fake_bsc.broadcast.assert_not_called()


# ------------------------------------------------------------------
# _submit_onchain_swap happy path
# ------------------------------------------------------------------

def test_submit_onchain_swap_happy_path():
    """v2.2.0: the floor's on-chain swap helper picks a fee tier,
    quotes, applies slippage, encodes calldata, ensures approval,
    signs, and broadcasts. Returns a dict with tx_hash + BscTrace URL."""
    policy = {"global_risk": {}}
    pf = Portfolio(starting_equity=Decimal("100"))

    # Mock the chain layer
    fake_bsc = MagicMock()
    fake_w3 = MagicMock()
    fake_erc20 = MagicMock()
    fake_erc20.functions.allowance.return_value.call.return_value = 0  # needs approval
    fake_erc20.functions.approve.return_value.build_transaction.return_value = {
        "data": "0x1234"
    }
    fake_w3.eth.contract.return_value = fake_erc20
    fake_bsc.w3.return_value = fake_w3
    fake_bsc.next_nonce.return_value = 5

    approval_receipt = MagicMock()
    approval_receipt.tx_hash = "0xapproval123"
    swap_receipt = MagicMock()
    swap_receipt.status = 1
    swap_receipt.tx_hash = "0xswap123abc"
    swap_receipt.block_number = 12345678
    swap_receipt.gas_used = 200000

    fake_bsc.broadcast.side_effect = [approval_receipt, swap_receipt]

    fake_pancake = MagicMock()
    fake_pancake.best_pool_fee.return_value = 2500  # 0.25%
    fake_pancake.quote.return_value = 100_000  # 1:1 for stub
    fake_pancake.encode_swap_v3.return_value = b"\x00" * 100

    fake_wallet = MagicMock()
    fake_wallet.address = "0xed669AE6632be9440cdACBE5ac5181D5BC871CC9"
    fake_wallet.sign_transaction.return_value = MagicMock()

    components = {
        "bsc": fake_bsc,
        "pancake": fake_pancake,
        "wallet": fake_wallet,
        "config": {
            "mode": "mainnet",
            "chain_id": 56,
            "dex": {
                "pcs_v3_router": "0x13f4EA83D0bd40E75C8222255bc855a974568Dd4",
                "pcs_v3_quoter": "0xB048Bbc1Ee6F73A7F6cB8152F8e3E2C3c4a5b6c7d",
                "pcs_v3_factory": "0x0BFbCF9a4FD9BFcb88F8C2E1fa7e7D5E5b5b5b5b5",
            },
            "gas": {"max_gwei": 5, "swap_gas": 250_000},
        },
    }
    agent = Agent(policy, pf, components=components)
    # Patch token_address to return deterministic addresses
    import core.utils as utils
    original = utils.token_address
    utils.token_address = lambda cfg, sym: (
        "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d" if sym == "USDC"
        else "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c" if sym == "WBNB"
        else "0x" + sym
    )

    try:
        result = asyncio.run(agent._submit_onchain_swap("USDC", Decimal("0.08")))
        assert result["status"] == "submitted"
        assert result["tx_hash"] == "0xswap123abc"
        assert "bsctrace.com/tx/0xswap123abc" in result["bsctrace_url"]
        assert result["amount_in_usdc"] == 0.08
        assert result["fee_tier_bps"] == 2500
        # Both approval and swap were broadcast
        assert fake_bsc.broadcast.call_count == 2
    finally:
        utils.token_address = original


# ------------------------------------------------------------------
# submit_floor_trade end-to-end (paper fallback)
# ------------------------------------------------------------------

def test_submit_floor_trade_paper_path_when_testnet():
    """v2.2.0: on testnet, the floor records a paper position, no
    tx_hash, no BscTrace URL."""
    policy = {"global_risk": {}}
    pf = Portfolio(starting_equity=Decimal("100"))
    components = {"config": {"mode": "testnet"}}
    agent = Agent(policy, pf, components=components)

    proposed = ProposedTrade(
        sleeve="B", symbol="USDC", side="buy",
        notional_usdc=Decimal("0.08"), risk_usdc=Decimal("0.0008"),
        is_new=True,
    )
    result = asyncio.run(agent.submit_floor_trade(proposed))
    assert result["status"] == "opened"
    assert result["onchain_tx_hash"] is None
    assert result["onchain_status"] is None
    assert result["bsctrace_url"] is None
    assert result["fallback_reason"] is None
    # The position is recorded in the portfolio
    assert len(pf.positions) == 1


def test_submit_floor_trade_onchain_path_when_mainnet():
    """v2.2.0: on mainnet with all components, the floor does a
    real on-chain swap and records the tx_hash + BscTrace URL on
    the dashboard."""
    policy = {"global_risk": {}}
    pf = Portfolio(starting_equity=Decimal("100"))

    fake_bsc = MagicMock()
    fake_w3 = MagicMock()
    fake_erc20 = MagicMock()
    fake_erc20.functions.allowance.return_value.call.return_value = 10**18
    fake_w3.eth.contract.return_value = fake_erc20
    fake_bsc.w3.return_value = fake_w3
    fake_bsc.next_nonce.return_value = 5
    fake_bsc.broadcast.return_value = MagicMock(
        status=1, tx_hash="0xfloor_swap_123",
        block_number=12345678, gas_used=180000,
    )
    fake_pancake = MagicMock()
    fake_pancake.best_pool_fee.return_value = 2500
    fake_pancake.quote.return_value = 100_000
    fake_pancake.encode_swap_v3.return_value = b"\x00" * 100
    fake_wallet = MagicMock()
    fake_wallet.address = "0xed669AE6632be9440cdACBE5ac5181D5BC871CC9"
    fake_wallet.sign_transaction.return_value = MagicMock()

    components = {
        "bsc": fake_bsc,
        "pancake": fake_pancake,
        "wallet": fake_wallet,
        "config": {
            "mode": "mainnet",
            "chain_id": 56,
            "dex": {
                "pcs_v3_router": "0x13f4EA83D0bd40E75C8222255bc855a974568Dd4",
                "pcs_v3_quoter": "0xB048Bbc1Ee6F73A7F6cB8152F8e3E2C3c4a5b6c7d",
                "pcs_v3_factory": "0x0BFbCF9a4FD9BFcb88F8C2E1fa7e7D5E5b5b5b5b5",
            },
            "gas": {"max_gwei": 5, "swap_gas": 250_000},
        },
    }
    agent = Agent(policy, pf, components=components)

    # Patch token_address for USDC + WBNB
    import core.utils as utils
    original = utils.token_address
    utils.token_address = lambda cfg, sym: (
        "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d" if sym == "USDC"
        else "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c" if sym == "WBNB"
        else "0x" + sym
    )

    try:
        proposed = ProposedTrade(
            sleeve="B", symbol="USDC", side="buy",
            notional_usdc=Decimal("0.08"), risk_usdc=Decimal("0.0008"),
            is_new=True,
        )
        result = asyncio.run(agent.submit_floor_trade(proposed))
        assert result["status"] == "opened"
        assert result["onchain_tx_hash"] == "0xfloor_swap_123"
        assert "bsctrace.com/tx/0xfloor_swap_123" in result["bsctrace_url"]
        assert result["onchain_status"] == "submitted"
        # The position is still recorded (paper-style bookkeeping)
        assert len(pf.positions) == 1
        # The dashboard state has the on-chain tx
        txs = agent.dashboard_state.get("floor_onchain_txs", [])
        assert len(txs) == 1
        assert txs[0]["tx_hash"] == "0xfloor_swap_123"
    finally:
        utils.token_address = original


def test_submit_floor_trade_falls_back_to_paper_on_onchain_failure():
    """v2.2.0: if the on-chain swap fails (e.g. broadcast error),
    the floor MUST still record a paper position so the 1-trade/day
    qualification is saved. Better a paper trade than no trade."""
    policy = {"global_risk": {}}
    pf = Portfolio(starting_equity=Decimal("100"))

    fake_bsc = MagicMock()
    fake_w3 = MagicMock()
    fake_erc20 = MagicMock()
    fake_erc20.functions.allowance.return_value.call.return_value = 10**18
    fake_w3.eth.contract.return_value = fake_erc20
    fake_bsc.w3.return_value = fake_w3
    fake_bsc.next_nonce.side_effect = RuntimeError("RPC unreachable")
    fake_pancake = MagicMock()
    fake_pancake.best_pool_fee.return_value = 2500
    fake_pancake.quote.return_value = 100_000
    fake_pancake.encode_swap_v3.return_value = b"\x00" * 100
    fake_wallet = MagicMock()
    fake_wallet.address = "0xed669AE6632be9440cdACBE5ac5181D5BC871CC9"

    components = {
        "bsc": fake_bsc,
        "pancake": fake_pancake,
        "wallet": fake_wallet,
        "config": {
            "mode": "mainnet", "chain_id": 56,
            "dex": {"pcs_v3_router": "0x13f4EA83D0bd40E75C8222255bc855a974568Dd4"},
            "gas": {"max_gwei": 5, "swap_gas": 250_000},
        },
    }
    agent = Agent(policy, pf, components=components)

    import core.utils as utils
    original = utils.token_address
    utils.token_address = lambda cfg, sym: (
        "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d" if sym == "USDC"
        else "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c" if sym == "WBNB"
        else "0x" + sym
    )

    try:
        proposed = ProposedTrade(
            sleeve="B", symbol="USDC", side="buy",
            notional_usdc=Decimal("0.08"), risk_usdc=Decimal("0.0008"),
            is_new=True,
        )
        result = asyncio.run(agent.submit_floor_trade(proposed))
        # Fallback happened
        assert result["onchain_status"] == "failed"
        assert "RPC unreachable" in (result.get("fallback_reason") or "")
        assert result["onchain_tx_hash"] is None
        # Position still recorded
        assert len(pf.positions) == 1
        # Dashboard state has NO on-chain tx (the attempt failed)
        txs = agent.dashboard_state.get("floor_onchain_txs", [])
        assert len(txs) == 0
    finally:
        utils.token_address = original