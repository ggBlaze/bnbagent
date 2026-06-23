"""#9 (v2.1.8): ~/.bnbagent/identity.json must be re-registered when
the wallet that boots the agent doesn't match the address saved in
the file.

The current code (core/boot.py:97-98) returns the saved identity
unconditionally if the file exists. Symptom: dashboard's /api/identity
shows agent_address=0xE65Fe14d... while the actual wallet (per
/api/wallet/balances) is 0xed669... — the operator imported a real
wallet AFTER the agent had already registered an identity for an
ephemeral one. Two-wallet confusion in the dashboard UI.

Fix: register_identity() compares the saved agent_address with the
current wallet.address. On mismatch, warn and re-register (the new
on-chain identity matches the wallet the operator actually controls).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


class _FakeWallet:
    def __init__(self, address: str):
        self.address = address


class _FakeERC8004:
    """Stub on-chain registrar. Counts registrations so tests can
    assert when register_identity() short-circuits vs re-runs."""
    def __init__(self):
        self.calls = 0
        # v2.3.0: register_identity() now also stores the registry
        # address in the saved identity so the dashboard can verify
        # the agent was registered against the 8004scan-indexed contract.
        self.registry = "0x" + "80" * 20
        self.tx_hash = "0x" + "ab" * 32
    def register(self, agent_uri: str):
        self.calls += 1
        return (1000 + self.calls, agent_uri)


class _FakeIPFS:
    def __init__(self):
        self.calls = 0
    def add_json(self, meta: dict) -> str:
        self.calls += 1
        return f"QmFakeCID{self.calls}"
    # v2.3.0: register_identity() now pins via pin_to_public_gateway
    # instead of add_json (the public-gateway URL is what 8004scan.io's
    # crawler can fetch). Stub it the same way so these tests stay
    # network-free.
    def pin_to_public_gateway(self, meta: dict) -> tuple[str, str]:
        self.calls += 1
        cid = f"QmFakeCID{self.calls}"
        return cid, f"https://example.com/ipfs/{cid}"


def _policy() -> dict:
    return {
        "evaluator_address": "0x" + "ev" * 20,
        "global_risk": {
            "max_gross_leverage": 1.0,
            "per_trade_risk_pct": 1.0,
            "daily_loss_circuit_breaker_pct": 5.0,
        },
    }


@pytest.fixture
def tmp_identity_home(monkeypatch, tmp_path):
    """Redirect ~/.bnbagent to a tmp dir so register_identity() writes
    a test-local file instead of the operator's real one."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    # Path("~/.bnbagent").expanduser() honors HOME env in posix.
    yield fake_home / ".bnbagent"


def test_identity_re_registered_when_wallet_address_changes(tmp_identity_home, monkeypatch):
    """The recorded scenario: first boot registers with ephemeral
    0xAAA..., operator imports real wallet, second boot must re-register
    so dashboard shows the operator's actual address — not the orphan
    ephemeral."""
    from core.boot import register_identity
    erc, ipfs = _FakeERC8004(), _FakeIPFS()
    policy = _policy()

    # First boot — ephemeral wallet
    ephemeral = _FakeWallet(address="0x" + "AA" * 20)
    first = register_identity(erc, ipfs, ephemeral, policy)
    assert first["agent_address"] == ephemeral.address
    assert erc.calls == 1

    # Second boot — operator's real wallet
    real = _FakeWallet(address="0x" + "BB" * 20)
    second = register_identity(erc, ipfs, real, policy)
    assert second["agent_address"] == real.address, (
        f"identity must update to the new wallet on mismatch; "
        f"still showing {second['agent_address']!r}"
    )
    assert erc.calls == 2, (
        f"register_identity should re-register on-chain when the wallet "
        f"changes; calls={erc.calls}"
    )


def test_identity_reused_when_wallet_address_matches(tmp_identity_home):
    """Normal restart: wallet didn't change, identity stays the same,
    no on-chain re-register (which would burn gas needlessly)."""
    from core.boot import register_identity
    erc, ipfs = _FakeERC8004(), _FakeIPFS()
    policy = _policy()
    wallet = _FakeWallet(address="0x" + "CC" * 20)
    first = register_identity(erc, ipfs, wallet, policy)
    second = register_identity(erc, ipfs, wallet, policy)
    assert first == second
    assert erc.calls == 1, (
        f"matching-wallet restart must NOT re-register; calls={erc.calls}"
    )


def test_identity_file_corrupt_re_registers(tmp_identity_home):
    """If identity.json is corrupt (bad JSON, truncated), don't crash —
    re-register from scratch. Same fallback as the file-missing path."""
    from core.boot import register_identity
    tmp_identity_home.mkdir(parents=True, exist_ok=True)
    (tmp_identity_home / "identity.json").write_text("{not valid json")
    erc, ipfs = _FakeERC8004(), _FakeIPFS()
    wallet = _FakeWallet(address="0x" + "DD" * 20)
    out = register_identity(erc, ipfs, wallet, _policy())
    assert out["agent_address"] == wallet.address
    assert erc.calls == 1


def test_identity_file_missing_field_re_registers(tmp_identity_home):
    """A pre-v2.1.8 identity.json without agent_address can't be
    validated — re-register rather than guess."""
    from core.boot import register_identity
    tmp_identity_home.mkdir(parents=True, exist_ok=True)
    (tmp_identity_home / "identity.json").write_text(json.dumps({
        "token_id": 42, "cid": "Qmtest",  # missing agent_address
        "evaluator_address": "0x...", "version": "0.9.0",
    }))
    erc, ipfs = _FakeERC8004(), _FakeIPFS()
    wallet = _FakeWallet(address="0x" + "EE" * 20)
    out = register_identity(erc, ipfs, wallet, _policy())
    assert out["agent_address"] == wallet.address
    assert erc.calls == 1
