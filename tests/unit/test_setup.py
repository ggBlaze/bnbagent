"""Tests for core/setup.py — the wizard's persistence layer.

The wizard's wallet import + generate paths must keep
`data_source.base_address` in `config/local.yaml` in sync with the
wallet address. core/boot.py already does this on agent start (lines
153-159). The wizard path was missing the same write, which left the
data-source step showing a stale placeholder (or "(create wallet
first)" if the field was empty).
"""
from __future__ import annotations

import json
import os
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from eth_account import Account


def _seed_config(tmp_path: Path, *, base_address_in_local: str | None = None) -> None:
    """Mirror install.sh: write a minimal config.yaml + optional local.yaml
    so the merged-config reader has something to deep-merge. Caller is
    responsible for monkeypatch.chdir(tmp_path) — see the fixtures below."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.safe_dump({
        "mode": "mainnet",
        "chain_id": 56,
        "rpcs": ["https://bsc-dataseed.binance.org"],
        "data_source": {
            "tier": "x402",
            "base_rpcs": ["https://mainnet.base.org"],
        },
        "cmc": {"x402_base": "https://api.coinmarketcap.com/agent-hub", "api_key": ""},
        "dex": {"pcs_v3_router": "0x" + "11" * 20,
                "pcs_v3_quoter": "0x" + "22" * 20,
                "pcs_v3_factory": "0x" + "33" * 20},
        "tokens": {"bsc_tokens": ["WBNB"]},
    }))
    local_payload: dict = {}
    if base_address_in_local is not None:
        local_payload.setdefault("data_source", {})["base_address"] = base_address_in_local
    (cfg_dir / "local.yaml").write_text(yaml.safe_dump(local_payload))


@pytest.fixture
def isolated_keystore(tmp_path, monkeypatch):
    """Point connectors.keystore at a tmp wallet.json so the test
    never touches the operator's real ~/.twak/wallet.json. Also chdir
    into tmp_path so core.config_paths resolves `config/local.yaml`
    there."""
    monkeypatch.chdir(tmp_path)
    keystore = tmp_path / "wallet.json"
    monkeypatch.setenv("TWAK_KEYSTORE", str(keystore))
    from connectors import keystore as ks
    ks._keystore_path = lambda: keystore
    return keystore


def test_import_wallet_writes_base_address_to_local_yaml(
    tmp_path, isolated_keystore
):
    """import_wallet() must write the imported wallet's address into
    `config/local.yaml` under data_source.base_address — the same
    write core/boot.py does on agent start. The wizard calls
    import_wallet BEFORE the agent boots (so the agent's boot-time
    write doesn't fire), and the data-source step reads
    data_source.base_address from the merged config. Without this
    write the step shows a stale placeholder."""
    _seed_config(tmp_path, base_address_in_local=None)

    # Generate a fresh key ONCE; derive both the pk we import AND the
    # expected address from it so the assertion can't drift.
    new_acct = Account.create()
    expected_addr = new_acct.address
    pk = "0x" + new_acct.key.hex()

    # IMPORTANT: import core.setup AFTER the env + chdir is set, so
    # the keystore path resolves to the tmp location.
    from core import setup as setup_mod
    from core import config_paths

    result = setup_mod.import_wallet(pk, "test-password-123")
    assert result["address"].lower() == expected_addr.lower()

    # Now read back the merged config and check base_address matches.
    merged = config_paths.load_config(base_dir=tmp_path)
    ds = merged.get("data_source", {})
    assert ds.get("base_address"), (
        "import_wallet should have written data_source.base_address "
        "to config/local.yaml"
    )
    assert ds["base_address"].lower() == expected_addr.lower(), (
        f"expected base_address={expected_addr}, got {ds.get('base_address')}"
    )


def test_generate_wallet_path_writes_base_address_to_local_yaml(
    tmp_path, isolated_keystore
):
    """generate_wallet() must also write the new wallet's address into
    data_source.base_address. Same reasoning as import_wallet.

    We exercise the helper directly (`_persist_base_address_if_unset`)
    rather than calling `generate_wallet()` itself because that function
    hits a pre-existing `Account.generate_mnemonic()` API mismatch
    (eth_account 0.13.x removed that name; the upstream bug is tracked
    separately). The base_address write is what we're testing here.
    """
    _seed_config(tmp_path, base_address_in_local=None)

    from core import setup as setup_mod
    from core import config_paths

    new_acct = Account.create()
    setup_mod._persist_base_address_if_unset(new_acct.address)

    merged = config_paths.load_config(base_dir=tmp_path)
    ds = merged.get("data_source", {})
    assert ds.get("base_address"), (
        "generate_wallet path should have written data_source.base_address "
        "to config/local.yaml"
    )
    assert ds["base_address"].lower() == new_acct.address.lower()


def test_persist_base_address_overwrites_stale_from_previous_boot(
    tmp_path, isolated_keystore
):
    """If local.yaml already has a stale base_address from a previous
    boot (typically the ephemeral key the agent fell back to when no
    keystore was present yet), import_wallet / generate_wallet MUST
    overwrite it with the freshly-imported/generated wallet's address.

    Without this, x402 balance polling would target the old ephemeral
    address instead of the operator's real wallet.
    """
    stale = "0x" + "9b" * 20   # looks like a valid checksummed address
    _seed_config(tmp_path, base_address_in_local=stale)

    from core import setup as setup_mod
    from core import config_paths

    new_acct = Account.create()
    setup_mod._persist_base_address_if_unset(new_acct.address)

    merged = config_paths.load_config(base_dir=tmp_path)
    assert merged["data_source"]["base_address"].lower() == new_acct.address.lower(), (
        f"import/generate must overwrite stale base_address; "
        f"got {merged['data_source'].get('base_address')!r}, "
        f"expected {new_acct.address!r}"
    )
