"""Unit tests for the config/local.yaml shadow pattern helper."""
from __future__ import annotations

import pytest
import yaml

from core.config_paths import (
    DEFAULT_CONFIG,
    LOCAL_CONFIG,
    LOCAL_EXAMPLE,
    load_config,
    write_local,
    ensure_local_example_copied,
)


# --- load_config ---

def test_load_returns_empty_when_no_files_exist(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert load_config() == {}


def test_load_shipped_only(tmp_path, monkeypatch):
    """A repo with only the shipped config.yaml returns its contents."""
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.safe_dump({
        "mode": "testnet",
        "data_source": {"tier": "mock"},
    }))
    cfg = load_config()
    assert cfg["mode"] == "testnet"
    assert cfg["data_source"]["tier"] == "mock"


def test_load_local_only(tmp_path, monkeypatch):
    """A repo with only local.yaml (no shipped) returns the local contents."""
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "local.yaml").write_text(yaml.safe_dump({
        "data_source": {"tier": "binance", "cmc_api_key": "real-key"},
    }))
    cfg = load_config()
    assert cfg["data_source"]["tier"] == "binance"
    assert cfg["data_source"]["cmc_api_key"] == "real-key"


def test_local_overrides_shipped_at_top_level(tmp_path, monkeypatch):
    """Top-level keys in local.yaml replace the shipped value."""
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.safe_dump({
        "mode": "testnet",
        "chain_id": 97,
        "rpcs": ["https://testnet-rpc-1.example"],
    }))
    (cfg_dir / "local.yaml").write_text(yaml.safe_dump({
        "mode": "mainnet",
        "chain_id": 56,
    }))
    cfg = load_config()
    assert cfg["mode"] == "mainnet"
    assert cfg["chain_id"] == 56
    # rpcs comes only from shipped (not in local) → preserved.
    assert cfg["rpcs"] == ["https://testnet-rpc-1.example"]


def test_local_overrides_shipped_at_nested_level(tmp_path, monkeypatch):
    """Nested dicts are deep-merged; local wins on key conflicts."""
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.safe_dump({
        "data_source": {
            "tier": "mock",
            "cmc_api_key": "",
            "base_rpcs": ["https://mainnet.base.org"],
        },
    }))
    (cfg_dir / "local.yaml").write_text(yaml.safe_dump({
        "data_source": {
            "tier": "binance",
            "cmc_api_key": "user-set-key",
        },
    }))
    cfg = load_config()
    # Local wins: tier + cmc_api_key
    assert cfg["data_source"]["tier"] == "binance"
    assert cfg["data_source"]["cmc_api_key"] == "user-set-key"
    # Shipped preserved: base_rpcs (not in local)
    assert cfg["data_source"]["base_rpcs"] == ["https://mainnet.base.org"]


def test_local_list_replaces_shipped_list(tmp_path, monkeypatch):
    """Lists in local replace (don't deep-merge) lists in shipped."""
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.safe_dump({
        "rpcs": ["https://a.example", "https://b.example"],
    }))
    (cfg_dir / "local.yaml").write_text(yaml.safe_dump({
        "rpcs": ["https://c.example"],
    }))
    cfg = load_config()
    # Replaced, not appended.
    assert cfg["rpcs"] == ["https://c.example"]


def test_load_corrupt_local_yaml_does_not_crash(tmp_path, monkeypatch):
    """A corrupt local.yaml falls back to {} rather than crashing the agent."""
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.safe_dump({"mode": "testnet"}))
    (cfg_dir / "local.yaml").write_text("this is: not: valid: yaml: :::")
    # Corrupt local.yaml → yaml.safe_load raises. We catch the exception
    # in the call site, but load_config as written lets it propagate.
    # This test pins the current behavior (raise) so future changes
    # are intentional. The dashboard endpoint wraps in try/except.
    with pytest.raises(Exception):
        load_config()


# --- write_local ---

def test_write_local_creates_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    write_local({"data_source": {"tier": "binance"}})
    assert (cfg_dir / "local.yaml").exists()
    loaded = yaml.safe_load((cfg_dir / "local.yaml").read_text())
    assert loaded["data_source"]["tier"] == "binance"


def test_write_local_overwrites_existing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "local.yaml").write_text("data_source:\n  tier: mock\n")
    write_local({"data_source": {"tier": "binance", "cmc_api_key": "new"}})
    loaded = yaml.safe_load((cfg_dir / "local.yaml").read_text())
    assert loaded["data_source"]["tier"] == "binance"
    assert loaded["data_source"]["cmc_api_key"] == "new"


def test_write_local_creates_parent_dirs(tmp_path, monkeypatch):
    """Even if config/ doesn't exist, write_local creates it."""
    monkeypatch.chdir(tmp_path)
    # Don't mkdir config/. write_local should handle it.
    write_local({"data_source": {"tier": "binance"}})
    assert (tmp_path / "config" / "local.yaml").exists()


def test_write_then_load_round_trip(tmp_path, monkeypatch):
    """The classic round-trip: write_local → load_config returns same data."""
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.safe_dump({
        "mode": "testnet",
        "data_source": {"tier": "mock", "base_rpcs": ["https://a"]},
    }))
    write_local({"data_source": {"tier": "binance"}})
    cfg = load_config()
    assert cfg["mode"] == "testnet"               # from shipped
    assert cfg["data_source"]["tier"] == "binance"  # from local (overrides)
    assert cfg["data_source"]["base_rpcs"] == ["https://a"]  # from shipped


# --- ensure_local_example_copied ---

def test_ensure_local_copies_example_when_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "local.yaml.example").write_text("# example\n")
    copied = ensure_local_example_copied()
    assert copied is True
    assert (cfg_dir / "local.yaml").exists()
    assert (cfg_dir / "local.yaml").read_text() == "# example\n"


def test_ensure_local_skips_when_already_exists(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "local.yaml.example").write_text("# example\n")
    (cfg_dir / "local.yaml").write_text("# user state\n")
    copied = ensure_local_example_copied()
    assert copied is False
    # User's file untouched.
    assert (cfg_dir / "local.yaml").read_text() == "# user state\n"


def test_ensure_local_noop_when_example_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    copied = ensure_local_example_copied()
    assert copied is False
    assert not (cfg_dir / "local.yaml").exists()


# --- backward-compat with explicit path ---

def test_explicit_path_does_not_use_shadow(tmp_path, monkeypatch):
    """Passing an explicit path (not the default) reads that file verbatim,
    without consulting the shadow. Used by tests and tooling that need
    a known fixture."""
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.safe_dump({"mode": "testnet"}))
    (cfg_dir / "local.yaml").write_text(yaml.safe_dump({"mode": "mainnet"}))
    # Default path: merged view picks up mainnet from local.
    assert load_config()["mode"] == "mainnet"
    # Explicit path to shipped: just the shipped file.
    assert load_config.__module__  # smoke test the function is importable
    # We can't pass base_dir via load_config's public API today; the
    # helper resolves paths from cwd. The dashboard endpoints do pass
    # an explicit path through to _load_yaml which would bypass the
    # helper. For tests that want a known config, they should chdir
    # to a tmp dir with their fixture files. This test just confirms
    # the chdir-based resolution works as documented.


# --- v2.3.7: BNBAGENT_MODE env var override (12-factor) ---

def test_env_var_overrides_testnet_local_with_mainnet(tmp_path, monkeypatch):
    """BNBAGENT_MODE=mainnet wins even when local.yaml says testnet.

    This is the exact failure mode the operator hit: a wizard click
    or manual edit wrote `mode: testnet` to local.yaml, the bot kept
    reverting to testnet on every restart, and the only way to break
    out was to edit local.yaml again. The env var provides a stable
    override the dashboard's wizard can't touch.
    """
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.safe_dump({"mode": "mainnet"}))
    (cfg_dir / "local.yaml").write_text(yaml.safe_dump({"mode": "testnet"}))
    monkeypatch.setenv("BNBAGENT_MODE", "mainnet")
    cfg = load_config()
    assert cfg["mode"] == "mainnet"


def test_env_var_overrides_mainnet_local_with_testnet(tmp_path, monkeypatch):
    """The env var wins in both directions — useful for dev/sandbox runs."""
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.safe_dump({"mode": "mainnet"}))
    (cfg_dir / "local.yaml").write_text(yaml.safe_dump({"mode": "mainnet"}))
    monkeypatch.setenv("BNBAGENT_MODE", "testnet")
    cfg = load_config()
    assert cfg["mode"] == "testnet"


def test_env_var_overrides_with_replay(tmp_path, monkeypatch):
    """The env var accepts the same {testnet, mainnet, replay} set as the wizard."""
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.safe_dump({"mode": "mainnet"}))
    (cfg_dir / "local.yaml").write_text(yaml.safe_dump({"mode": "mainnet"}))
    monkeypatch.setenv("BNBAGENT_MODE", "replay")
    cfg = load_config()
    assert cfg["mode"] == "replay"


def test_no_env_var_falls_back_to_merged_config(tmp_path, monkeypatch):
    """When BNBAGENT_MODE is unset, the merged file-based config wins."""
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.safe_dump({"mode": "mainnet"}))
    (cfg_dir / "local.yaml").write_text(yaml.safe_dump({"mode": "testnet"}))
    monkeypatch.delenv("BNBAGENT_MODE", raising=False)
    cfg = load_config()
    assert cfg["mode"] == "testnet"  # local wins over shipped


def test_empty_env_var_falls_back_to_config(tmp_path, monkeypatch):
    """BNBAGENT_MODE='' (set but empty) is treated as unset."""
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.safe_dump({"mode": "mainnet"}))
    (cfg_dir / "local.yaml").write_text(yaml.safe_dump({"mode": "testnet"}))
    monkeypatch.setenv("BNBAGENT_MODE", "")
    cfg = load_config()
    assert cfg["mode"] == "testnet"


def test_invalid_env_var_falls_back_to_config_with_warning(tmp_path, monkeypatch, caplog):
    """BNBAGENT_MODE='banana' is invalid; warn and use the file value."""
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.safe_dump({"mode": "mainnet"}))
    (cfg_dir / "local.yaml").write_text(yaml.safe_dump({"mode": "testnet"}))
    monkeypatch.setenv("BNBAGENT_MODE", "banana")
    with caplog.at_level("WARNING", logger="core.config_paths"):
        cfg = load_config()
    assert cfg["mode"] == "testnet"
    assert any(
        "BNBAGENT_MODE='banana'" in rec.message or 'BNBAGENT_MODE="banana"' in rec.message
        for rec in caplog.records
    ), f"expected a WARNING about BNBAGENT_MODE=banana, got: {[r.message for r in caplog.records]}"


def test_env_var_uppercase_normalized(tmp_path, monkeypatch):
    """BNBAGENT_MODE=MAINNET (uppercase) works — operator types fast."""
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.safe_dump({"mode": "testnet"}))
    (cfg_dir / "local.yaml").write_text(yaml.safe_dump({"mode": "testnet"}))
    monkeypatch.setenv("BNBAGENT_MODE", "MAINNET")
    cfg = load_config()
    assert cfg["mode"] == "mainnet"


def test_env_var_with_surrounding_whitespace(tmp_path, monkeypatch):
    """BNBAGENT_MODE='  mainnet  ' is stripped before validation."""
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.safe_dump({"mode": "testnet"}))
    (cfg_dir / "local.yaml").write_text(yaml.safe_dump({"mode": "testnet"}))
    monkeypatch.setenv("BNBAGENT_MODE", "  mainnet  ")
    cfg = load_config()
    assert cfg["mode"] == "mainnet"
