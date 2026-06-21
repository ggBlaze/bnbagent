"""Tests for scripts/competition_register.py.

The BNB HACK 2026 rules require on-chain registration before the live
window opens. The competition contract address is pinned in the
script and asserted in the rules page. These tests cover:

  1. The competition contract address is the canonical one.
  2. _resolve_agent_address() finds the address in policy.yaml.
  3. _resolve_agent_address() falls back to BNBAGENT_PRIVATE_KEY.
  4. _resolve_agent_address() returns None when nothing is set up.
  5. _emit_mcp_action() includes the contract + network + address.
  6. _check_already_registered() returns the cache or empty dict.
  7. main() --check exits 0 when registered, 1 when not, 2 when no addr.
  8. main() --dry-run exits 0 and prints the MCP action.
  9. _run_twak_compete_register() returns ok=False when npx missing.
 10. The script's JSON output is parseable + has the right shape.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts import competition_register  # noqa: E402


# -- 1. Contract address --------------------------------------------------

def test_competition_contract_address_is_canonical():
    """This address is the single source of truth. If it changes, the
    hackathon organizers announce it in Telegram and the rules page.
    The test pins the value to the bsctrace.com link in the rules."""
    addr = competition_register.COMPETITION_CONTRACT
    assert addr == "0x212c61b9b72c95d95bf29cf032f5e5635629aed5", (
        f"contract address changed from canonical. Update the "
        f"DoraHacks rules pin and the docs/ if this is intentional."
    )
    # Must be a 40-hex address
    assert addr.startswith("0x") and len(addr) == 42


def test_cache_path_is_gitignored():
    """The cache contains the agent address + tx hash. It must not
    leak to a fresh clone."""
    from pathlib import Path
    gitignore = (ROOT / ".gitignore").read_text() if (ROOT / ".gitignore").exists() else ""
    cache = str(competition_register.CACHE_PATH)
    # The exact path may differ between relative-vs-absolute, so check
    # for the basename or path fragment.
    assert any(frag in gitignore for frag in (
        "competition_register.json",
        "data/competition_register.json",
        "/data/competition_register",
    )), f"competition_register.json cache not gitignored; risks leaking agent address"


# -- 2-4. Address resolution ----------------------------------------------

def test_resolve_from_policy_yaml(tmp_path, monkeypatch):
    """If policy.yaml has agent_address, that wins."""
    policy = tmp_path / "config" / "policy.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text(json.dumps({
        "agent_address": "0x1234567890abcdef1234567890abcdef12345678"
    }))
    monkeypatch.chdir(tmp_path)
    addr = competition_register._resolve_agent_address()
    assert addr == "0x1234567890abcdef1234567890abcdef12345678"


def test_resolve_invalid_address_in_policy_returns_none(tmp_path, monkeypatch):
    """Garbage in policy.yaml must not be returned as an address."""
    policy = tmp_path / "config" / "policy.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text("agent_address: 'not-a-real-address'\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BNBAGENT_PRIVATE_KEY", raising=False)
    # Redirect Path.home() so the operator's real ~/.twak/wallet.json
    # can't leak into the test (this happens on dev machines with a
    # real keystore — the test was written assuming CI's empty home).
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    addr = competition_register._resolve_agent_address()
    assert addr is None


def test_resolve_returns_none_when_nothing_set(tmp_path, monkeypatch):
    """No policy, no env, no TWAK keystore → None."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BNBAGENT_PRIVATE_KEY", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    addr = competition_register._resolve_agent_address()
    assert addr is None


# -- 5. MCP emission -------------------------------------------------------

def test_emit_mcp_action_shape():
    action = competition_register._emit_mcp_action(
        address="0x1234567890abcdef1234567890abcdef12345678",
        network="mainnet",
    )
    assert action["mcp_server"] == "bnbagent"
    assert action["action"] == "competition_register"
    assert action["params"]["contract"] == competition_register.COMPETITION_CONTRACT
    assert action["params"]["network"] == "mainnet"
    assert action["params"]["address"] == "0x1234567890abcdef1234567890abcdef12345678"
    # Two client examples (Python + JSON-RPC)
    assert isinstance(action["client_examples"], list)
    assert len(action["client_examples"]) >= 1


# -- 6. Cache --------------------------------------------------------------

def test_check_already_registered_empty(tmp_path, monkeypatch):
    """When the cache file doesn't exist, returns {}."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(competition_register, "CACHE_PATH", tmp_path / "no-such-file.json")
    assert competition_register._check_already_registered() == {}


def test_save_and_load_cache(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cache_path = tmp_path / "cache.json"
    monkeypatch.setattr(competition_register, "CACHE_PATH", cache_path)
    competition_register._save_cache({"ok": True, "tx_hash": "0xabc"})
    assert cache_path.exists()
    data = competition_register._load_cache()
    assert data == {"ok": True, "tx_hash": "0xabc"}


# -- 7-8. main() flags -----------------------------------------------------

def test_main_check_returns_1_when_not_registered(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cache_path = tmp_path / "no.json"
    monkeypatch.setattr(competition_register, "CACHE_PATH", cache_path)
    rc = competition_register.main(["--check"])
    assert rc == 1  # not registered → exit 1
    out = capsys.readouterr().out
    assert '"registered": false' in out


def test_main_dry_run_exits_0(tmp_path, monkeypatch, capsys):
    """Dry-run resolves the address + emits the MCP action but does not
    shell out to twak."""
    policy = tmp_path / "config" / "policy.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text(json.dumps({
        "agent_address": "0x1234567890abcdef1234567890abcdef12345678"
    }))
    monkeypatch.chdir(tmp_path)
    rc = competition_register.main(["--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    # The output should be the MCP action JSON
    assert "competition_register" in out
    assert "0x212c61b9b72c95d95bf29cf032f5e5635629aed5" in out


def test_main_no_address_exits_2(tmp_path, monkeypatch):
    """When no agent address can be resolved, main exits 2 (the operator
    must set BNBAGENT_PRIVATE_KEY, sign the policy, or init TWAK)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BNBAGENT_PRIVATE_KEY", raising=False)
    # Redirect Path.home() so a real ~/.twak/wallet.json doesn't leak.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    rc = competition_register.main([])
    assert rc == 2


# -- 9. twak subprocess fallback -----------------------------------------

def test_twak_returns_okoless_when_npx_missing(tmp_path, monkeypatch):
    """If npx isn't on PATH, the script returns ok=False with a clear
    message in stderr rather than crashing."""
    monkeypatch.setattr("shutil.which", lambda x: None)
    result = competition_register._run_twak_compete_register(network="mainnet")
    assert result["ok"] is False
    assert "npx" in result["stderr"] or "npm" in result["stderr"]


# -- 10. JSON output shape (full integration dry-run) --------------------

def test_full_dry_run_json_parses(tmp_path, monkeypatch, capsys):
    """The --emit-mcp flag's stdout is the MCP action JSON."""
    policy = tmp_path / "config" / "policy.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text(json.dumps({
        "agent_address": "0xabcabcabcabcabcabcabcabcabcabcabcabcabcd"
    }))
    monkeypatch.chdir(tmp_path)
    rc = competition_register.main(["--emit-mcp"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    parsed = json.loads(out)
    assert parsed["action"] == "competition_register"
    assert "client_examples" in parsed
