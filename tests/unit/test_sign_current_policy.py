"""Tests for core/setup.sign_current_policy.

The wizard's step 5 (Sign Policy) calls /api/setup/sign which
delegates here. After a Reset Everything (which wipes policy.yaml
via reset_setup()), the operator reaches step 5 and clicks Sign
with their password — expecting it to just work. The current code
refuses with FileNotFoundError("config/policy.yaml missing —
generate first"), forcing the operator to drop to a shell and
run policy.policy_sign manually. This is a UX cliff.

The fix: if policy.yaml is missing, generate it from the template
in policy/policy_sign.py (DEFAULT_POLICY_BODY) using the unlocked
wallet's address for evaluator_address + agent_address, then sign.
"""
from __future__ import annotations

import json
import yaml
import pytest
from eth_account import Account


def _seed_minimal_config(tmp_path):
    """Write the minimal config/config.yaml that policy_sign uses
    to resolve defaults. We don't actually need it for sign, but
    reset_setup deletes config.yaml too so we keep both files in
    sync in the tmp dir."""
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "config.yaml").write_text(yaml.safe_dump({
        "mode": "mainnet",
        "chain_id": 56,
        "rpcs": ["https://bsc-dataseed.binance.org"],
        "data_source": {"tier": "x402", "base_rpcs": ["https://mainnet.base.org"]},
        "cmc": {"x402_base": "https://api.coinmarketcap.com/agent-hub", "api_key": ""},
        "dex": {"pcs_v3_router": "0x" + "11" * 20,
                "pcs_v3_quoter": "0x" + "22" * 20,
                "pcs_v3_factory": "0x" + "33" * 20},
        "tokens": {"bsc_tokens": ["WBNB"]},
    }))
    return cfg


@pytest.fixture
def chdir_and_keystore(tmp_path, monkeypatch):
    """chdir to tmp_path + point keystore at a tmp wallet.json so
    the test never touches the operator's real keystore."""
    monkeypatch.chdir(tmp_path)
    keystore = tmp_path / "wallet.json"
    monkeypatch.setenv("TWAK_KEYSTORE", str(keystore))
    from connectors import keystore as ks
    ks._keystore_path = lambda: keystore
    _seed_minimal_config(tmp_path)
    return tmp_path


def _import_test_wallet(tmp_path, password="test-pwd-12345"):
    """Create a fresh key, encrypt to the tmp keystore, return the
    expected address."""
    from core import setup as setup_mod
    new_acct = Account.create()
    pk = "0x" + new_acct.key.hex()
    setup_mod.import_wallet(pk, password)
    return new_acct.address


def test_sign_generates_policy_when_missing(chdir_and_keystore):
    """After a Reset Everything, config/policy.yaml is deleted. The
    next /api/setup/sign call must generate it from the template +
    sign it, instead of raising FileNotFoundError."""
    from core import setup as setup_mod

    expected_addr = _import_test_wallet(chdir_and_keystore)
    # Sanity: policy.yaml does NOT exist (reset wiped it).
    assert not (chdir_and_keystore / "config" / "policy.yaml").exists()

    # Sign — must NOT raise FileNotFoundError.
    result = setup_mod.sign_current_policy("test-pwd-12345")
    assert result.get("signature", "").startswith("0x")
    assert result["signature"] != "0x" + "00" * 65

    # policy.yaml should now exist and be signed by the wallet.
    pol_path = chdir_and_keystore / "config" / "policy.yaml"
    assert pol_path.exists()
    doc = yaml.safe_load(pol_path.read_text())
    assert doc["agent_address"].lower() == expected_addr.lower()
    assert doc["evaluator_address"].lower() == expected_addr.lower()
    assert doc["signature"] == result["signature"]
    # has all the required policy fields
    for key in ("version", "issued_at", "expires_at",
                "global_risk", "sleeve_allocations", "sleeves",
                "allowlist", "fees"):
        assert key in doc, f"generated policy missing {key!r}"


def test_sign_signs_existing_policy_without_overwriting_evaluator(chdir_and_keystore):
    """If policy.yaml already exists, sign_current_policy must NOT
    regenerate it (which would clobber any operator edits). It just
    signs whatever is there and writes back the signature."""
    from core import setup as setup_mod

    expected_addr = _import_test_wallet(chdir_and_keystore)

    # Pre-write a policy.yaml with a CUSTOM evaluator (multi-sig
    # scenario). sign_current_policy must preserve it.
    pol_path = chdir_and_keystore / "config" / "policy.yaml"
    custom_eval = "0x" + "ab" * 20
    custom_agent = expected_addr  # signer is the agent
    doc = {
        "version": "1.0.0",
        "issued_at": 1_700_000_000,
        "expires_at": 1_700_000_000 + 30 * 86400,
        "evaluator_address": custom_eval,
        "agent_address": custom_agent,
        "global_risk": {"daily_loss_circuit_breaker_pct": 3.0},
        "sleeve_allocations": {"A": 0.7, "B": 0.2, "C": 0.1},
        "sleeves": {"A": {}, "B": {}, "C": {}},
        "allowlist": {},
        "fees": {},
        "signature": "0x" + "00" * 65,
    }
    pol_path.write_text(yaml.safe_dump(doc))

    result = setup_mod.sign_current_policy("test-pwd-12345")
    saved = yaml.safe_load(pol_path.read_text())
    # Evaluator preserved (multi-sig), agent preserved, signature set
    assert saved["evaluator_address"].lower() == custom_eval.lower()
    assert saved["agent_address"].lower() == custom_agent.lower()
    assert saved["signature"] == result["signature"]
