"""Test that boot() returns a data_source component and no longer exposes a cmc one."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from core.boot import boot

import pytest


@pytest.fixture(autouse=True)
def _protect_real_local_yaml():
    """Belt-and-suspenders: snapshot the user's real config/local.yaml
    before each test in this file and restore it after, in case a
    future test regresses and writes to it without scoping.

    Scope-limited to this file (autouse=True within this module only)
    so it doesn't slow down the rest of the suite.
    """
    real = Path("config/local.yaml").resolve()
    backup = real.read_bytes() if real.exists() else None
    try:
        yield
    finally:
        if backup is not None:
            real.write_bytes(backup)
        elif real.exists():
            real.unlink()


def _write_config(tmp_path: Path, ds: dict) -> Path:
    cfg = {
        "mode": "replay",
        "data_source": ds,
        "cmc": {"x402_base": "https://api.coinmarketcap.com/agent-hub", "api_key": ""},
        "rpcs": ["http://localhost:8545"],
        "chain_id": 97,
        "dex": {"pcs_v3_router": "0x" + "11" * 20,
                "pcs_v3_quoter": "0x" + "22" * 20,
                "pcs_v3_factory": "0x" + "33" * 20},
        "tokens": {"bsc_tokens": ["WBNB"]},
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return p


def _write_policy(tmp_path: Path) -> Path:
    pol = tmp_path / "policy.yaml"
    pol.write_text(
        "version: 1.0.0\n"
        "issued_at: 1781244077\n"
        "expires_at: 1783836077\n"
        "evaluator_address: '0xf9725D032e166E09f4d5c740046A65E36575bB0f'\n"
        "agent_address: '0xf9725D032e166E09f4d5c740046A65E36575bB0f'\n"
        "global_risk:\n"
        "  daily_loss_circuit_breaker_pct: 3.0\n"
        "  per_trade_risk_pct: 1.0\n"
        "  max_gross_leverage: 2.0\n"
        "  max_single_position_pct: 15.0\n"
        "  max_daily_trades: 100\n"
        "  max_drawdown_pct: 8.0\n"
        "  cooldown_after_breach_min: 60\n"
        "sleeve_allocations:\n"
        "  A: 0.7\n"
        "  B: 0.2\n"
        "  C: 0.1\n"
        "sleeves:\n"
        "  A: {enabled: true, venue_selection: highest_abs_funding_7d, rebalance_hours: 8, fund_floor_pct: 0.005, basis_trigger_pct: 0.5, max_position_pct: 15.0}\n"
        "  B: {enabled: true, volume_spike_mult: 2.0, breakout_lookback_h: 4, atr_len: 14, atr_stop_mult: 2.0, tp_pct: 3.0, max_hold_min: 240, kelly_fraction: 0.25, max_position_pct: 10.0}\n"
        "  C: {enabled: true, zscore_threshold: 2.5, stop_pct: 2.0, target_pct: 1.0, lookback_h: 1, kelly_fraction: 0.25, max_position_pct: 5.0}\n"
        "allowlist:\n"
        "  cmc_rank_max: 50\n"
        "  bsc_tokens: [WBNB, USDC, CAKE]\n"
        "  perps_venues: [aster, killex, apollox, mux]\n"
        "  dex_routers: ['0x9A489505a6B3cd73B4D6C8E6B3E8a3e7B9C8d2e1']\n"
        "fees:\n"
        "  x402_max_usdc_per_day: '10.00'\n"
        "  max_gas_price_gwei: 5\n"
        "signature: '0x0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000'\n"
    )
    return pol


def test_boot_returns_data_source_router(tmp_path: Path, monkeypatch):
    from connectors.data_source import DataSourceRouter
    from core import boot as boot_mod

    # Bypass identity registration (no IPFS in tests) and any network calls.
    monkeypatch.setattr(boot_mod, "register_identity", lambda *a, **kw: {
        "token_id": 0, "cid": "QmTest", "agent_address": "0x" + "00" * 20,
        "evaluator_address": "0x" + "00" * 20, "version": "1.0.0",
    })

    cfg = _write_config(tmp_path, {"tier": "mock"})
    pol = _write_policy(tmp_path)
    c = boot(Decimal("100"), policy_path=str(pol), config_path=str(cfg), replay_tape=[])
    assert "data_source" in c
    assert isinstance(c["data_source"], DataSourceRouter)
    assert c["data_source"].tier == "mock"


def test_boot_data_source_no_longer_exposes_cmc(tmp_path: Path, monkeypatch):
    from core import boot as boot_mod

    monkeypatch.setattr(boot_mod, "register_identity", lambda *a, **kw: {
        "token_id": 0, "cid": "QmTest", "agent_address": "0x" + "00" * 20,
        "evaluator_address": "0x" + "00" * 20, "version": "1.0.0",
    })

    cfg = _write_config(tmp_path, {"tier": "mock"})
    pol = _write_policy(tmp_path)
    c = boot(Decimal("100"), policy_path=str(pol), config_path=str(cfg), replay_tape=[])
    assert "cmc" not in c


def test_boot_default_tier_is_mock(tmp_path: Path, monkeypatch):
    """No data_source config block at all — boot should still succeed with mock."""
    from connectors.data_source import DataSourceRouter
    from core import boot as boot_mod

    monkeypatch.setattr(boot_mod, "register_identity", lambda *a, **kw: {
        "token_id": 0, "cid": "QmTest", "agent_address": "0x" + "00" * 20,
        "evaluator_address": "0x" + "00" * 20, "version": "1.0.0",
    })

    cfg = _write_config(tmp_path, {})  # no data_source block
    pol = _write_policy(tmp_path)
    c = boot(Decimal("100"), policy_path=str(pol), config_path=str(cfg), replay_tape=[])
    assert "data_source" in c
    assert isinstance(c["data_source"], DataSourceRouter)
    assert c["data_source"].tier == "mock"


def test_boot_writes_base_address_to_local_yaml(tmp_path: Path, monkeypatch):
    """Boot writes the wallet's address to data_source.base_address so
    /api/data-source/x402-balance works without a query param.

    BSC and Base share the same secp256k1 address format, so
    wallet.address IS the Base address.

    v2.1.1: boot writes to config/local.yaml (the user-state shadow),
    not the shipped config/config.yaml. The shipped file is treated
    as immutable at runtime. This test sets up a tmp/config/ fixture
    and verifies the write lands in tmp/config/local.yaml.
    """
    from core import boot as boot_mod
    from core import config_paths

    # Point config_paths at our tmp dir for both read and write.
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "config.yaml"
    cfg_file.write_text(yaml.safe_dump({
        "mode": "replay",
        "data_source": {"tier": "x402", "base_rpcs": ["https://mainnet.base.org"]},
        "cmc": {"x402_base": "https://api.coinmarketcap.com/agent-hub", "api_key": ""},
        "rpcs": ["http://localhost:8545"],
        "chain_id": 97,
        "dex": {"pcs_v3_router": "0x" + "11" * 20,
                "pcs_v3_quoter": "0x" + "22" * 20,
                "pcs_v3_factory": "0x" + "33" * 20},
        "tokens": {"bsc_tokens": ["WBNB"]},
    }))
    pol = _write_policy(tmp_path)
    # load_policy() also reads config/policy.schema.json (relative to cwd)
    # so copy the repo's schema into the tmp dir. Same for perps_venues.yaml
    # (the perps connector reads it on init).
    import shutil
    shutil.copy("config/policy.schema.json", cfg_dir / "policy.schema.json")
    shutil.copy("config/perps_venues.yaml", cfg_dir / "perps_venues.yaml")
    shutil.copy("config/allowlist.yaml", cfg_dir / "allowlist.yaml")
    monkeypatch.setattr(boot_mod, "register_identity", lambda *a, **kw: {
        "token_id": 0, "cid": "QmTest", "agent_address": "0x" + "00" * 20,
        "evaluator_address": "0x" + "00" * 20, "version": "1.0.0",
    })

    # Use chdir + default path so the shadow pattern is exercised.
    monkeypatch.chdir(tmp_path)
    boot(Decimal("100"), policy_path=str(pol), replay_tape=[])

    # Re-read the merged view from disk; base_address should now be set
    # (boot wrote it to local.yaml).
    new_cfg = config_paths.load_config()
    assert new_cfg["data_source"].get("base_address"), (
        "boot should have written wallet.address to local.yaml's data_source.base_address"
    )
    # And the wallet address from boot is what got written.
    from connectors.twak import TWAKWallet
    from eth_account import Account
    expected = Account.create().address  # not the actual value; just a sanity check
    # We can't predict the exact value (ephemeral key), but it should
    # look like a 0x-prefixed 20-byte address.
    assert new_cfg["data_source"]["base_address"].startswith("0x")
    assert len(new_cfg["data_source"]["base_address"]) == 42


def test_boot_resyncs_base_address_when_stale(tmp_path: Path, monkeypatch):
    """v2.1.8: boot always writes wallet.address to data_source.base_address,
    even when local.yaml already has a (possibly stale) value. This prevents
    drift when the wizard or a manual edit left a non-wallet address there.
    """
    from core import boot as boot_mod
    from core import config_paths

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    # Pre-populate local.yaml with a STALE address so we can verify boot
    # overwrites it (the previous behavior skipped the write because the
    # value was already set).
    (cfg_dir / "local.yaml").write_text(yaml.safe_dump({
        "data_source": {
            "tier": "x402",
            "base_address": "0xSTALE0000000000000000000000000000000000",
        },
    }))
    cfg_file = cfg_dir / "config.yaml"
    cfg_file.write_text(yaml.safe_dump({
        "mode": "replay",
        "data_source": {"tier": "x402", "base_rpcs": ["https://mainnet.base.org"]},
        "cmc": {"x402_base": "https://api.coinmarketcap.com/agent-hub", "api_key": ""},
        "rpcs": ["http://localhost:8545"],
        "chain_id": 97,
        "dex": {"pcs_v3_router": "0x" + "11" * 20,
                "pcs_v3_quoter": "0x" + "22" * 20,
                "pcs_v3_factory": "0x" + "33" * 20},
        "tokens": {"bsc_tokens": ["WBNB"]},
    }))
    pol = _write_policy(tmp_path)
    import shutil
    shutil.copy("config/policy.schema.json", cfg_dir / "policy.schema.json")
    shutil.copy("config/perps_venues.yaml", cfg_dir / "perps_venues.yaml")
    shutil.copy("config/allowlist.yaml", cfg_dir / "allowlist.yaml")
    monkeypatch.setattr(boot_mod, "register_identity", lambda *a, **kw: {
        "token_id": 0, "cid": "QmTest", "agent_address": "0x" + "00" * 20,
        "evaluator_address": "0x" + "00" * 20, "version": "1.0.0",
    })
    monkeypatch.chdir(tmp_path)
    boot(Decimal("100"), policy_path=str(pol), replay_tape=[])

    new_cfg = config_paths.load_config()
    addr = new_cfg["data_source"]["base_address"]
    assert addr != "0xSTALE0000000000000000000000000000000000", (
        f"boot should have overwritten stale base_address; got {addr}"
    )
    assert addr.startswith("0x") and len(addr) == 42
