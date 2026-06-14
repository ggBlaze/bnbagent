"""Unit tests for core/balances.py.

We don't hit real RPCs here \u2014 we monkeypatch _connect_first to return a
fake Web3-like object that knows how to answer `get_balance` and
`balanceOf` from a hardcoded table. That keeps the test deterministic
and offline.
"""
from __future__ import annotations

import pytest

from core import balances


# --- helpers --------------------------------------------------------------

class _FakeContractFunctions:
    """Mimics web3's Contract.functions property: a namespace of bound methods."""
    def __init__(self, balance): self._balance = balance
    def balanceOf(self, _addr): return _FakeCall(self._balance)

class _FakeContract:
    def __init__(self, balance):
        self._balance = balance
    @property
    def functions(self):
        return _FakeContractFunctions(self._balance)

class _FakeCall:
    def __init__(self, val): self._val = val
    def call(self): return self._val

class _FakeWeb3:
    def __init__(self, chain_id=56, native_balance_wei=0, token_balances=None):
        self.eth = _FakeEth(chain_id, native_balance_wei, token_balances or {})

class _FakeEth:
    def __init__(self, chain_id, native_balance_wei, token_balances):
        self._chain_id = chain_id
        self._native = native_balance_wei
        self._tokens = token_balances
    @property
    def chain_id(self): return self._chain_id
    def get_balance(self, _addr): return self._native
    def contract(self, address, abi):
        # Match by lowercased address; missing => zero
        return _FakeContract(self._tokens.get(address.lower(), 0))


def _patch_connect(monkeypatch, fake_w3):
    monkeypatch.setattr(balances, "_connect_first", lambda rpcs, timeout=5.0: fake_w3)


# --- tests ----------------------------------------------------------------

def test_get_wallet_balances_no_wallet():
    b = balances.get_wallet_balances("", ["https://example.com"], 56)
    assert b.wallet == ""
    assert "no wallet" in b.error


def test_get_wallet_balances_no_rpcs():
    b = balances.get_wallet_balances("0xAbc", [], 56)
    assert "no BSC RPCs" in b.error


# Use a 40-hex char address (valid EOA shape) so to_checksum_address works
WALLET = "0x" + "a" * 40
WALLET_CHECKSUM = "0x" + "a" * 40  # all-lower is a valid (non-checksummed) form


def test_get_wallet_balances_bsc_only(monkeypatch):
    fake = _FakeWeb3(
        chain_id=56,
        native_balance_wei=10**18,                    # 1 BNB
        token_balances={
            # USDT 0x55d3... mainnet, 100 USDT (18 decimals)
            "0x55d398326f99059ff775485246999027b3197955": 100 * 10**18,
            # USDC 0x8ac7... mainnet, 50 USDC (18 decimals)
            "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": 50 * 10**18,
            # BUSD 0xe9e7... mainnet, 0 (not in the dict => 0)
        },
    )
    _patch_connect(monkeypatch, fake)
    b = balances.get_wallet_balances(WALLET, ["https://example.com"], 56)
    assert b.wallet == WALLET
    assert b.base is None  # base_active=False default
    assert b.bsc.error == ""
    # Native
    assert b.bsc.native.symbol == "BNB"
    assert b.bsc.native.balance == "1"
    # Tokens
    syms = {t.symbol: t for t in b.bsc.tokens}
    assert syms["USDT"].balance == "100"
    assert syms["USDT"].usd == 100.0  # stable
    assert syms["USDC"].balance == "50"
    assert syms["USDC"].usd == 50.0
    assert syms["BUSD"].balance == "0"
    assert syms["BUSD"].usd == 0.0


def test_get_wallet_balances_with_base_when_x402(monkeypatch):
    # Two fake RPCs: BSC and Base
    bsc_fake = _FakeWeb3(chain_id=56, native_balance_wei=2 * 10**18)
    base_fake = _FakeWeb3(chain_id=8453, native_balance_wei=10**16)  # 0.01 ETH
    monkeypatch.setattr(
        balances, "_connect_first",
        lambda rpcs, timeout=5.0: bsc_fake if "bsc" in rpcs[0] else base_fake,
    )
    b = balances.get_wallet_balances(
        WALLET,
        ["https://bsc.example.com"],
        56,
        base_active=True,
        base_rpcs=["https://base.example.com"],
    )
    assert b.base_active is True
    assert b.base is not None
    assert b.base.native.symbol == "ETH"
    assert b.base.native.balance == "0.01"


def test_get_wallet_balances_no_rpc_reachable(monkeypatch):
    monkeypatch.setattr(balances, "_connect_first", lambda rpcs, timeout=5.0: None)
    b = balances.get_wallet_balances(WALLET, ["https://broken"], 56)
    assert b.bsc.error == "no BSC RPC reachable"


def test_balances_to_dict_shape():
    b = balances.WalletBalances(
        wallet="0xabc", chain_id=56, base_active=False, fetched_at=12345,
    )
    d = balances.balances_to_dict(b)
    assert d["wallet"] == "0xabc"
    assert d["chain_id"] == 56
    assert d["bsc"]["native"] is None
    assert d["bsc"]["tokens"] == []
    assert d["base"] is None
    assert d["base_active"] is False
    assert d["fetched_at"] == 12345


def test_wei_to_human_trims_trailing_zeros():
    assert balances._wei_to_human(1500000000000000000, 18) == "1.5"
    assert balances._wei_to_human(1000000, 6) == "1"
    assert balances._wei_to_human(0, 18) == "0"
    assert balances._wei_to_human(None, 18) == "0"


def test_pick_token_addr_uses_mainnet_or_testnet():
    usdt = balances.BSC_TOKENS[0]  # USDT
    assert balances._pick_token_addr(usdt, 56) == usdt[1]
    assert balances._pick_token_addr(usdt, 97) == usdt[2]
    assert balances._pick_token_addr(usdt, 1) == ""  # unknown chain
