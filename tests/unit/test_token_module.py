"""Unit tests for TokenModule."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from agents.token_module import TokenModule
from connectors.twak import TWAKWallet


@pytest.fixture(autouse=True)
def unlock_token_deploy(monkeypatch):
    """v2.1.6: the TokenModule has a hard date lock until 2026-07-07 UTC
    + an env opt-in. The lock logic is tested in test_token_lock.py;
    these tests focus on the rest of the module's behavior, so we
    auto-unlock the date lock for every test in this file."""
    fake_now = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(TokenModule, "_now_utc",
                        classmethod(lambda cls: fake_now))
    monkeypatch.setenv("BNBAGENT_ALLOW_TOKEN_DEPLOY", "true")


@pytest.fixture
def wallet():
    return TWAKWallet.from_private_key("0x" + "a" * 64)


@pytest.fixture
def bsc_client():
    from connectors.bnb_sdk import BSCClient
    return BSCClient(rpcs=["https://testnet.example"], chain_id=97, mode="testnet")


@pytest.fixture
def components(wallet, bsc_client, tmp_path):
    return {
        "wallet": wallet,
        "bsc": bsc_client,
        "data_source": None,
        "ipfs": None,
    }


@pytest.fixture
def config_path(tmp_path):
    p = tmp_path / "token_module.yaml"
    p.write_text("network: testnet\nprotocol: erc20_minimal\ndefault_supply: \"1000000\"\ndefault_decimals: 18\ncreate_website: false\n")
    return str(p)


@pytest.mark.asyncio
async def test_create_token_returns_valid_address(components, config_path):
    tm = TokenModule(components=components, config_path=config_path)
    r = await tm.create_token(name="Mooncoin", symbol="MOON", supply=1_000_000)
    assert r.contract_address.startswith("0x")
    assert len(r.contract_address) == 42
    assert r.tx_hash.startswith("0x")
    assert r.symbol == "MOON"
    assert r.total_supply == 1_000_000
    assert r.network == "testnet"


@pytest.mark.asyncio
async def test_bytecode_under_8kb(components, config_path):
    tm = TokenModule(components=components, config_path=config_path)
    init = tm._build_init_code("erc20_minimal", "X", "X", 18, 1000)
    # Our stub is 256 bytes; ABI-encoded args add ~200 bytes; well under 8KB.
    assert len(init) < 8192


@pytest.mark.asyncio
async def test_symbol_length_validated(components, config_path):
    tm = TokenModule(components=components, config_path=config_path)
    with pytest.raises(ValueError, match="symbol"):
        await tm.create_token(name="X", symbol="AB", supply=1)


@pytest.mark.asyncio
async def test_mainnet_requires_explicit_network(components, config_path):
    tm = TokenModule(components=components, config_path=config_path)
    with pytest.raises(ValueError, match="network"):
        await tm.create_token(name="X", symbol="ABC", supply=1, network="bsc")


@pytest.mark.asyncio
async def test_supply_must_be_positive(components, config_path):
    tm = TokenModule(components=components, config_path=config_path)
    with pytest.raises(ValueError, match="supply"):
        await tm.create_token(name="X", symbol="ABC", supply=0)


# --- v2.0.8-L5: stable testnet stub bytecode seed -----------------------

def test_stub_bytecode_stable_across_calls(components, config_path):
    """v2.0.8-L5: the testnet stub bytecode is stable across calls.

    The previous seed was time-based (f"bnbagent:{protocol}:{time.time() // 86400}")
    so the stub changed daily. The new seed is a fixed string, so
    two calls in the same process produce the same stub. Two calls
    on different days also produce the same stub.
    """
    tm = TokenModule(components=components, config_path=config_path)
    # wipe the cache so we go to the fallback path
    tm._init_code_cache = {}
    b1 = tm._load_runtime("erc20_minimal")
    b2 = tm._load_runtime("erc20_minimal")
    assert b1 == b2
    # 32 bytes (keccak) * 4 repetitions = 128 bytes
    assert len(b1) == 128


def test_stub_bytecode_stable_across_protocols(components, config_path):
    """Different protocols get different stubs (the protocol name is in the seed)."""
    tm = TokenModule(components=components, config_path=config_path)
    tm._init_code_cache = {}
    a = tm._load_runtime("erc20_minimal")
    b = tm._load_runtime("bep20")
    c = tm._load_runtime("openzeppelin")
    assert a != b
    assert b != c
    assert a != c


def test_sanitize_website_strips_eval(components, config_path):
    tm = TokenModule(components=components, config_path=config_path)
    bad = '<script>eval("alert(1)")</script>Function("x")() document.write("pwned")'
    out = tm._sanitize_website(bad)
    assert "eval" not in out
    assert "Function" not in out
    assert "document.write" not in out


def test_sanitize_website_strips_external_script_src(components, config_path):
    tm = TokenModule(components=components, config_path=config_path)
    bad = '<script src="https://evil.example/x.js"></script><p>safe</p>'
    out = tm._sanitize_website(bad)
    assert "evil.example" not in out
    assert "safe" in out


def test_sanitize_website_strips_event_handlers(components, config_path):
    tm = TokenModule(components=components, config_path=config_path)
    bad = '<button onclick="alert(1)">click</button>'
    out = tm._sanitize_website(bad)
    assert "onclick" not in out


def test_sanitize_website_empty_returns_empty(components, config_path):
    tm = TokenModule(components=components, config_path=config_path)
    assert tm._sanitize_website("") == ""


def test_fallback_website_has_no_external_resources(components, config_path):
    tm = TokenModule(components=components, config_path=config_path)
    html = tm._fallback_website("X", "X", "0x" + "a" * 40, "minimal")
    assert "https://" not in html.replace("https://bscscan.com", "").replace("https://testnet.bscscan.com", "")
    # Actually the fallback includes explorer links — check no external JS/CSS
    assert "<script src" not in html
    assert "googleapis" not in html
    assert "google" not in html.lower()


def test_explorer_url_mainnet_vs_testnet(components, config_path):
    tm = TokenModule(components=components, config_path=config_path)
    main = tm._explorer_url("0x" + "a" * 64, "mainnet")
    test = tm._explorer_url("0x" + "a" * 64, "testnet")
    assert "bscscan.com" in main
    assert "testnet.bscscan.com" in test


def test_update_config_merges(components, config_path):
    tm = TokenModule(components=components, config_path=config_path)
    tm.update_config({"create_website": True, "default_supply": "5000000"})
    assert tm.config["create_website"] is True
    assert tm.config["default_supply"] == "5000000"
    assert tm.config["network"] == "testnet"  # untouched
    # the file is persisted
    assert "create_website: true" in Path(config_path).read_text()


def test_update_config_rejects_unknown_keys(components, config_path):
    tm = TokenModule(components=components, config_path=config_path)
    tm.update_config({"unknown_key": "x"})
    assert "unknown_key" not in tm.config


@pytest.mark.asyncio
async def test_create_token_emits_explorer_url(components, config_path):
    tm = TokenModule(components=components, config_path=config_path)
    r = await tm.create_token(name="X", symbol="ABC", supply=1_000_000)
    assert r.explorer_url.startswith("https://testnet.bscscan.com/tx/")
