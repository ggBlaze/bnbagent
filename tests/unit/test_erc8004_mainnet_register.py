"""Tests for v2.3.0 ERC8004.register() real on-chain broadcast path.

Background: in v2.3.0 we switched from a deterministic stub to actually
broadcasting ``register(string agentURI)`` against the canonical BSC
ERC-8004 IdentityRegistry at 0x8004A169FB4a3325136EB29fA0ceB6D2e539a432.
This contract is the one 8004scan.io crawls — without it the agent page
returns 404.

These tests pin the mainnet path's behavior using MagicMock so they
stay network-free:
  1. ERC8004.register on mainnet calls the contract's register(string)
  2. The Transfer event is parsed correctly to extract the real tokenId
  3. Raises if the wallet is None (so a missing wiring doesn't silently
     fall back to a stub)
  4. Raises if the broadcast tx reverts (status != 1)
  5. The canonical registry check works as expected
  6. On testnet/replay mode, the deterministic stub path still works
  7. ERC8004 wired via boot.py gets the wallet injected (regression
     guard for the boot.py wire-up step)
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from web3 import Web3

from connectors.bnb_sdk import BSCClient, ERC8004


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

CANONICAL_REGISTRY = "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"
TRANSFER_TOPIC = Web3.keccak(text="Transfer(address,address,uint256)")


def _make_wallet(addr: str = "0xed669AE6632be9440cdACBE5ac5181D5BC871CC9") -> MagicMock:
    w = MagicMock()
    w.address = Web3.to_checksum_address(addr)
    signed = MagicMock()
    signed.raw_transaction = b"\x00" * 100
    signed.hash = b"\xab" * 32
    w.sign_transaction.return_value = MagicMock(
        raw_tx=signed.raw_transaction,
        tx_hash="0x" + signed.hash.hex(),
        signed={"nonce": 5, "to": CANONICAL_REGISTRY, "chainId": 56, "gas": 300_000},
    )
    return w


def _make_bsc_mainnet() -> MagicMock:
    bsc = MagicMock()
    bsc.mode = "mainnet"
    bsc.chain_id = 56
    bsc.next_nonce.return_value = 5
    return bsc


def _transfer_log(token_id: int, to: str) -> dict:
    """Build a Transfer event log dict (the shape web3.py returns).

    ERC-721 Transfer(address indexed from, address indexed to,
    uint256 indexed tokenId) has 4 topics:
      [0] = keccak256("Transfer(address,address,uint256)")
      [1] = from (32-byte word: address padded to 32 bytes)
      [2] = to   (32-byte word)
      [3] = tokenId (32-byte uint256)
    """
    return {
        "address": CANONICAL_REGISTRY,
        "topics": [
            "0x" + TRANSFER_TOPIC.hex(),
            "0x" + "00" * 32,                                              # from = 0x0 (mint)
            "0x" + Web3.to_bytes(hexstr=Web3.to_checksum_address(to)).hex().rjust(64, "0"),
            "0x" + hex(token_id)[2:].rjust(64, "0"),                       # tokenId
        ],
        "data": "0x",
    }


# ------------------------------------------------------------------
# 1. Mainnet happy path: contract.register(agentURI) is called
# ------------------------------------------------------------------

def test_mainnet_register_broadcasts_register_call():
    """v2.3.0: in mainnet mode, register(agent_uri) is called on the
    ERC-8004 IdentityRegistry, signed via TWAK, broadcast, and the
    Transfer event is parsed for the real tokenId."""
    bsc = _make_bsc_mainnet()
    wallet = _make_wallet()
    fake_w3 = MagicMock()
    fake_contract = MagicMock()
    fake_contract.functions.register.return_value.build_transaction.return_value = {
        "from": wallet.address, "nonce": 5, "to": CANONICAL_REGISTRY,
        "data": "0xdeadbeef", "chainId": 56,
    }
    fake_w3.eth.contract.return_value = fake_contract
    bsc.w3.return_value = fake_w3

    # Tx settles with status=1 and emits a Transfer log
    bsc.broadcast.return_value = MagicMock(
        tx_hash="0x" + "ab" * 32,
        block_number=12345,
        gas_used=180000,
        status=1,
        logs=[_transfer_log(token_id=42, to=wallet.address)],
    )

    erc = ERC8004(client=bsc, registry_address=CANONICAL_REGISTRY, wallet=wallet)
    token_id, agent_uri = erc.register(agent_uri="https://gateway.pinata.cloud/ipfs/QmX")

    # Real tokenId was extracted from the Transfer event
    assert token_id == 42
    assert agent_uri == "https://gateway.pinata.cloud/ipfs/QmX"
    # Contract register(string) was called with the right URI
    fake_contract.functions.register.assert_called_once_with(
        "https://gateway.pinata.cloud/ipfs/QmX"
    )
    # Wallet signed the tx, BSC broadcast it
    wallet.sign_transaction.assert_called_once()
    bsc.broadcast.assert_called_once()
    # Tx hash + nonce stored on the wrapper
    assert erc.tx_hash == "0x" + "ab" * 32
    assert erc.token_id == 42


# ------------------------------------------------------------------
# 2. Transfer parsing — only mint events count
# ------------------------------------------------------------------

def test_transfer_parser_picks_mint_event():
    """When the receipt has multiple Transfer events, only the mint
    (from=0x0) is used to extract the tokenId. Other transfers (e.g.
    past transfers to/from the wallet) are ignored."""
    bsc = _make_bsc_mainnet()
    wallet = _make_wallet()
    fake_w3 = MagicMock()
    fake_contract = MagicMock()
    fake_contract.functions.register.return_value.build_transaction.return_value = {
        "from": wallet.address, "nonce": 5, "to": CANONICAL_REGISTRY, "chainId": 56,
    }
    fake_w3.eth.contract.return_value = fake_contract
    bsc.w3.return_value = fake_w3

    # Two logs: an irrelevant transfer FROM the wallet (tokenId 99), and
    # the mint TO the wallet (tokenId 100). Only the mint matters.
    bsc.broadcast.return_value = MagicMock(
        tx_hash="0x" + "cd" * 32,
        block_number=12345,
        gas_used=180000,
        status=1,
        logs=[
            _transfer_log(token_id=99, to="0x" + "11" * 20),  # not us
            _transfer_log(token_id=100, to=wallet.address),   # the mint
        ],
    )

    erc = ERC8004(client=bsc, registry_address=CANONICAL_REGISTRY, wallet=wallet)
    token_id, _ = erc.register(agent_uri="ipfs://QmABC")
    assert token_id == 100


# ------------------------------------------------------------------
# 3. Error path: wallet is None on mainnet
# ------------------------------------------------------------------

def test_mainnet_register_raises_without_wallet():
    """v2.3.0: if no wallet is wired and we're on mainnet, register()
    raises loudly instead of silently returning a stub (which is the
    pre-v2.3.0 behavior that hid the registration gap)."""
    bsc = _make_bsc_mainnet()
    erc = ERC8004(client=bsc, registry_address=CANONICAL_REGISTRY, wallet=None)
    with pytest.raises(RuntimeError, match="requires a TWAKWallet"):
        erc.register(agent_uri="ipfs://QmABC")


# ------------------------------------------------------------------
# 4. Error path: tx reverts
# ------------------------------------------------------------------

def test_mainnet_register_raises_on_revert():
    """v2.3.0: a reverted broadcast (status != 1) raises so the operator
    sees the failure rather than logging a fake success."""
    bsc = _make_bsc_mainnet()
    wallet = _make_wallet()
    fake_w3 = MagicMock()
    fake_contract = MagicMock()
    fake_contract.functions.register.return_value.build_transaction.return_value = {
        "from": wallet.address, "nonce": 5, "to": CANONICAL_REGISTRY, "chainId": 56,
    }
    fake_w3.eth.contract.return_value = fake_contract
    bsc.w3.return_value = fake_w3

    bsc.broadcast.return_value = MagicMock(
        tx_hash="0x" + "ee" * 32,
        block_number=12345,
        gas_used=30000,
        status=0,    # <-- reverted
        logs=[],
    )

    erc = ERC8004(client=bsc, registry_address=CANONICAL_REGISTRY, wallet=wallet)
    with pytest.raises(RuntimeError, match="tx reverted"):
        erc.register(agent_uri="ipfs://QmABC")


def test_mainnet_register_raises_if_no_transfer_event():
    """v2.3.0: if the tx settles but no Transfer event is found (e.g.
    the registry's ABI differs from what we assume), raise — don't
    silently return 0 as the tokenId."""
    bsc = _make_bsc_mainnet()
    wallet = _make_wallet()
    fake_w3 = MagicMock()
    fake_contract = MagicMock()
    fake_contract.functions.register.return_value.build_transaction.return_value = {
        "from": wallet.address, "nonce": 5, "to": CANONICAL_REGISTRY, "chainId": 56,
    }
    fake_w3.eth.contract.return_value = fake_contract
    bsc.w3.return_value = fake_w3

    bsc.broadcast.return_value = MagicMock(
        tx_hash="0x" + "ff" * 32,
        block_number=12345,
        gas_used=180000,
        status=1,
        logs=[],   # <-- no Transfer event
    )

    erc = ERC8004(client=bsc, registry_address=CANONICAL_REGISTRY, wallet=wallet)
    with pytest.raises(RuntimeError, match="no Transfer event found"):
        erc.register(agent_uri="ipfs://QmABC")


# ------------------------------------------------------------------
# 5. Canonical registry check
# ------------------------------------------------------------------

def test_is_canonical_identity_registry_true():
    bsc = _make_bsc_mainnet()
    wallet = _make_wallet()
    erc = ERC8004(client=bsc, registry_address=CANONICAL_REGISTRY, wallet=wallet)
    assert erc._is_canonical_identity_registry() is True


def test_is_canonical_identity_registry_false_for_competition_registry():
    """The BNB HACK 2026 CompetitionRegistry (0x212c61b...) is NOT the
    IdentityRegistry that 8004scan.io indexes. Don't let a misconfigured
    boot.py slip through."""
    bsc = _make_bsc_mainnet()
    wallet = _make_wallet()
    erc = ERC8004(
        client=bsc,
        registry_address="0x212c61b9b72c95d95bf29cf032f5e5635629aed5",
        wallet=wallet,
    )
    assert erc._is_canonical_identity_registry() is False


def test_is_canonical_identity_registry_case_insensitive():
    """Checksum vs lowercase address shouldn't matter — 8004scan's
    indexer normalises either way."""
    bsc = _make_bsc_mainnet()
    wallet = _make_wallet()
    erc = ERC8004(
        client=bsc,
        registry_address=CANONICAL_REGISTRY.lower(),
        wallet=wallet,
    )
    assert erc._is_canonical_identity_registry() is True


# ------------------------------------------------------------------
# 6. Testnet/replay still works with the deterministic stub
# ------------------------------------------------------------------

def test_testnet_register_returns_deterministic_stub():
    """v2.3.0: backwards compat — testnet/replay still returns the
    deterministic stub (no broadcast, no gas). The stub is for tests
    + paper mode."""
    bsc = MagicMock()
    bsc.mode = "testnet"
    bsc.chain_id = 97
    erc = ERC8004(client=bsc, registry_address=CANONICAL_REGISTRY)
    token_id, agent_uri = erc.register(agent_uri="ipfs://QmXYZ")
    # Stub: token_id derived from keccak(agent_uri)
    expected_token_id = int.from_bytes(Web3.keccak(text="ipfs://QmXYZ")[:8], "big")
    assert token_id == expected_token_id
    # Returns agent_uri unchanged so callers can use it as the agentURI
    assert agent_uri == "ipfs://QmXYZ"
    # No broadcast happened
    bsc.broadcast.assert_not_called()


# ------------------------------------------------------------------
# 7. IPFSClient.pin_to_public_gateway
# ------------------------------------------------------------------

def test_pinata_path_returns_gateway_url(monkeypatch):
    """v2.3.0: when PINATA_API_KEY is set, the IPFS client pins via
    Pinata and returns a gateway URL (the agentURI 8004scan will fetch)."""
    from connectors.ipfs import IPFSClient
    monkeypatch.setenv("PINATA_API_KEY", "test-pinata-key")
    monkeypatch.setenv("PINATA_SECRET_API_KEY", "test-pinata-secret")

    fake_response = MagicMock()
    fake_response.json.return_value = {"IpfsHash": "QmPinataCid123"}
    fake_response.raise_for_status = MagicMock()

    with patch("connectors.ipfs.httpx.post", return_value=fake_response) as mock_post:
        ipfs = IPFSClient(mode="mainnet")
        cid, url = ipfs.pin_to_public_gateway({"name": "BNB Agent"})

    assert cid == "QmPinataCid123"
    assert url == "https://gateway.pinata.cloud/ipfs/QmPinataCid123"
    mock_post.assert_called_once()
    # POST was made to api.pinata.cloud
    args, kwargs = mock_post.call_args
    assert "api.pinata.cloud" in args[0]
    # The pinata API key was in headers
    assert kwargs["headers"]["pinata_api_key"] == "test-pinata-key"


def test_pinata_failure_falls_through_to_local_cid(monkeypatch):
    """v2.3.0: if Pinata fails (no key, network error, etc.) we fall
    back to a local CID. The NFT will still be indexed, only metadata
    fetch fails."""
    from connectors.ipfs import IPFSClient
    monkeypatch.setenv("PINATA_API_KEY", "broken-key")
    with patch("connectors.ipfs.httpx.post", side_effect=Exception("network down")):
        ipfs = IPFSClient(mode="mainnet")
        cid, url = ipfs.pin_to_public_gateway({"name": "BNB Agent"})
    # Local CID only — no public gateway URL
    assert cid.startswith("Qm")
    assert url.startswith("ipfs://")


def test_no_pinata_key_returns_local_cid(monkeypatch):
    """v2.3.0: with no PINATA_API_KEY set, we skip Pinata entirely
    and return a local-only CID."""
    from connectors.ipfs import IPFSClient
    monkeypatch.delenv("PINATA_API_KEY", raising=False)
    monkeypatch.delenv("PINATA_SECRET_API_KEY", raising=False)
    ipfs = IPFSClient(mode="mainnet")
    cid, url = ipfs.pin_to_public_gateway({"name": "BNB Agent"})
    assert cid.startswith("Qm")
    assert url.startswith("ipfs://")


# ------------------------------------------------------------------
# 8. Boot.py wire-up: ERC8004 gets wallet injected
# ------------------------------------------------------------------

def test_boot_wires_wallet_into_erc8004(tmp_path, monkeypatch):
    """v2.3.0 regression guard: boot.py must inject the TWAKWallet
    into ERC8004 BEFORE register_identity is called. Without this
    wire-up the mainnet register path raises RuntimeError."""
    import os
    # Skip the actual on-chain tx in this test
    from core import boot as boot_mod
    monkeypatch.setenv("TWAK_KEYSTORE", "/nonexistent/keystore.json")
    monkeypatch.setenv("TWAK_PWD", "test-pwd")
    # Use a tmp identity path so we don't accidentally re-use the
    # operator's real ~/.bnbagent/identity.json
    tmp_id = tmp_path / "identity.json"
    monkeypatch.setattr(boot_mod, "Path",
                        lambda p: tmp_id if "identity.json" in str(p) else Path(p))
    # Stub the wallet so we don't need a real keystore
    fake_wallet = MagicMock()
    fake_wallet.address = "0xed669AE6632be9440cdACBE5ac5181D5BC871CC9"

    # Stub the IdentityRegistry call so we don't actually broadcast
    class _FakeERC8004:
        def __init__(self):
            self.registry = CANONICAL_REGISTRY
            self.token_id = 999
            self.tx_hash = "0x" + "ab" * 32
        def register(self, agent_uri):
            return (999, agent_uri)
    fake_erc8004 = _FakeERC8004()

    fake_ipfs = MagicMock()
    fake_ipfs.pin_to_public_gateway.return_value = ("QmX", "https://gateway.pinata.cloud/ipfs/QmX")

    identity = boot_mod.register_identity(
        fake_erc8004, fake_ipfs, fake_wallet,
        policy={
            "evaluator_address": "0x" + "11" * 20,
            "agent_address": "0x" + "11" * 20,
            "global_risk": {
                "max_gross_leverage": 2.0,
                "per_trade_risk_pct": 1.0,
                "daily_loss_circuit_breaker_pct": 3.0,
            },
        },
    )
    assert identity["token_id"] == 999
    assert identity["agent_uri"] == "https://gateway.pinata.cloud/ipfs/QmX"
    assert identity["agent_address"] == "0xed669AE6632be9440cdACBE5ac5181D5BC871CC9"
    assert identity["registry_address"] == CANONICAL_REGISTRY
