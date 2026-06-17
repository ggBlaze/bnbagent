"""Tests for core/setup.reset_setup().

The Reset Everything button calls /api/setup/reset which delegates
here. It must wipe OPERATOR STATE (signed policy, keystore,
~/.bnbagent/setup.json) but NOT the shipped defaults file
config/config.yaml — that's a tracked file with the bootstrap
RPCs, base_rpcs, etc. Without it the x402 balance polling
endpoint returns 422 "no base_rpcs configured" because the
default base_rpcs list only lives in config.yaml.

This test catches the regression where reset_setup deletes
config.yaml by accident. The fix: the reset list must include
ONLY gitignored state files (or files the operator created).
"""
from __future__ import annotations

import pytest
import yaml
from pathlib import Path


@pytest.fixture
def chdir_and_seed(tmp_path, monkeypatch):
    """chdir to tmp_path + lay down a minimal repo layout so
    reset_setup has something to operate on. Also overrides the
    SUMMARY_PATH module constant (which defaults to ~/.bnbagent)
    to point at tmp_path/.bnbagent so we can assert it was removed."""
    monkeypatch.chdir(tmp_path)
    from connectors import keystore as ks
    keystore = tmp_path / "wallet.json"
    monkeypatch.setenv("TWAK_KEYSTORE", str(keystore))
    ks._keystore_path = lambda: keystore
    # Redirect the ~/.bnbagent/setup.json summary path to tmp_path too
    # so we can verify it was deleted (the user's home dir is sacred).
    summary = tmp_path / ".bnbagent" / "setup.json"
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text("{}")
    import core.setup as setup_mod
    monkeypatch.setattr(setup_mod, "SUMMARY_PATH", summary)
    cfg = tmp_path / "config"
    cfg.mkdir()
    # Tracked defaults (must NOT be deleted)
    (cfg / "config.yaml").write_text(yaml.safe_dump({
        "mode": "mainnet",
        "rpcs": ["https://bsc-dataseed.binance.org"],
        "data_source": {
            "tier": "x402",
            "base_rpcs": ["https://mainnet.base.org"],
        },
    }))
    # Other tracked config (must NOT be deleted)
    (cfg / "allowlist.yaml").write_text("tokens: []\n")
    (cfg / "policy.yaml.example").write_text("template: |\n")
    # Gitignored state files (SHOULD be deleted by reset)
    (cfg / "local.yaml").write_text("mode: mainnet\n")
    (cfg / "policy.yaml").write_text("version: 1.0.0\nsignature: __SIG__\n")
    # Keystore (the file the keystore helper resolves to)
    keystore.write_text("{}")
    return tmp_path


def test_reset_does_not_delete_tracked_config_yaml(chdir_and_seed):
    """config/config.yaml is TRACKED in git — it holds the shipped
    defaults. reset_setup() must not wipe it, otherwise the agent
    loses its base_rpcs / RPC defaults and x402 polling breaks."""
    from core import setup as setup_mod
    result = setup_mod.reset()

    config_yaml = chdir_and_seed / "config" / "config.yaml"
    assert config_yaml.exists(), (
        "reset_setup deleted config/config.yaml — it's a tracked file "
        "and must survive reset"
    )
    # The reset should also report what it removed — and config.yaml
    # must NOT be in the list.
    removed = [Path(p) for p in result.get("removed", [])]
    assert config_yaml not in removed


def test_reset_does_not_delete_other_tracked_files(chdir_and_seed):
    """Defensive: only gitignored STATE files should be removed.
    Note: we don't assert each specific state file is gone (those
    paths are ~env-dependent — the keystore and summary helpers
    resolve to ~/.twak / ~/.bnbagent) — we just check the tracked
    files survive. The point of this test is the regression guard."""
    from core import setup as setup_mod
    setup_mod.reset()

    # Tracked files that must survive
    for rel in ("config/config.yaml",
                "config/allowlist.yaml",
                "config/policy.yaml.example"):
        assert (chdir_and_seed / rel).exists(), f"reset wiped tracked file: {rel}"


def test_x402_poll_recovers_after_reset(chdir_and_seed):
    """End-to-end-ish: after a reset, the merged config from
    config_paths must still expose base_rpcs (because config.yaml
    survives)."""
    from core import setup as setup_mod
    from core import config_paths

    setup_mod.reset()
    merged = config_paths.load_config(base_dir=chdir_and_seed)
    assert merged["data_source"]["base_rpcs"] == ["https://mainnet.base.org"], (
        "after reset, base_rpcs from shipped config.yaml must still "
        "be readable so x402 polling works"
    )
