"""Operator setup: persisted config + policy + keystore.

The dashboard's "Setup" wizard writes through this module. The agent's boot
sequence reads from the same files. Single source of truth on disk.

State on disk:
  config/config.yaml      — shipped defaults (tracked in git, immutable
                            at runtime). Mode, chain, RPCs, token registry,
                            data_source defaults. See config/config.yaml.
  config/local.yaml       — user-specific overrides (gitignored). Tier
                            choice, CMC Pro API key, custom Base RPCs +
                            address, anything the Setup wizard persists.
                            See config/local.yaml.example for the shape
                            and core/config_paths.py for the merge
                            semantics (deep-merge, local wins).
  config/policy.yaml      — signed User Policy
  ~/.twak/wallet.json     — encrypted wallet keystore
  ~/.bnbagent/setup.json  — operator-friendly summary (what the dashboard reads)

The wizard reads the merged view via core.config_paths.load_config() and
writes back via core.config_paths.write_local(). The shipped config.yaml
is never mutated at runtime.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml
from eth_account import Account
from web3 import Web3

from connectors.keystore import (
    _keystore_path, create_keystore, import_keystore, load_keystore_summary,
    unlock_and_get_account, KeystoreCorrupt,
)
from policy.policy_sign import sign_policy
from .config_paths import (
    load_config as _load_merged_config,
    write_local,
    DEFAULT_CONFIG,
    LOCAL_CONFIG,
)


# --- on-disk summary -------------------------------------------------------

SUMMARY_PATH = Path("~/.bnbagent/setup.json").expanduser()


@dataclass
class SetupState:
    mode: str = "testnet"
    chain_id: int = 97
    rpcs: list[str] = field(default_factory=lambda: [
        "https://data-seed-prebsc-1-s1.binance.org:8545",
        "https://data-seed-prebsc-2-s1.binance.org:8545",
    ])
    cmc_api_key: str = ""
    cmc_x402_base: str = "https://api.coinmarketcap.com/agent-hub"
    wallet_address: str = ""
    keystore_path: str = ""
    keystore_error: str = ""     # v2.0.8-L4: human-readable error if the keystore is corrupt
    evaluator_address: str = ""
    policy_signed: bool = False
    policy_signature: str = ""
    policy_version: str = ""
    updated_at: int = 0

    def is_complete(self) -> bool:
        return (
            bool(self.wallet_address)
            and bool(self.evaluator_address)
            and self.policy_signed
            and self.chain_id in (56, 97)
        )

    def missing(self) -> list[str]:
        m = []
        if not self.wallet_address: m.append("wallet")
        if not self.evaluator_address: m.append("evaluator address")
        if not self.policy_signed:   m.append("signed policy")
        if self.chain_id not in (56, 97): m.append("chain id")
        return m


# --- load / save -----------------------------------------------------------

def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}


def _save_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, default_flow_style=False))


def load_setup_state() -> SetupState:
    """Read everything from disk and assemble the operator summary.

    v2.0.8-L4: distinguish keystore-missing (None) from keystore-corrupt
    (KeystoreCorrupt). The SetupState.wallet_address is empty in both
    cases, but the exception is captured separately so the dashboard
    can show 'keystore is corrupt, here's how to recover' instead of
    silently treating the corrupt file as 'no wallet'.

    v2.1.1: use the local.yaml shadow pattern. Read the merged view
    of (shipped config.yaml) + (user-specific local.yaml) so the
    dashboard's Step 1 reflects whatever the user actually configured
    in the wizard, not just the shipped defaults.
    """
    cfg = _load_merged_config()
    pol = _load_yaml(Path("config/policy.yaml"))
    # v2.0.8-L4: catch KeystoreCorrupt separately from missing-file
    try:
        ks = load_keystore_summary()
        ks_error = None
    except KeystoreCorrupt as e:
        ks = None
        ks_error = str(e)
    sig = str(pol.get("signature", "") or "")
    state = SetupState(
        mode=str(cfg.get("mode", "testnet")),
        chain_id=int(cfg.get("chain_id", 97)),
        rpcs=list(cfg.get("rpcs", []) or []),
        cmc_api_key=str(cfg.get("cmc", {}).get("api_key", "") or ""),
        cmc_x402_base=str(cfg.get("cmc", {}).get("x402_base", "https://api.coinmarketcap.com/agent-hub")),
        wallet_address=ks.get("address", "") if ks else "",
        keystore_path=ks.get("path", "") if ks else "",
        keystore_error=ks_error or "",   # v2.0.8-L4: human-readable if corrupt
        evaluator_address=str(pol.get("evaluator_address", "") or ""),
        policy_signed=sig.startswith("0x") and sig != "0x" + "00" * 65,
        policy_signature=sig,
        policy_version=str(pol.get("version", "")),
        updated_at=int(time.time()),
    )
    return state


# --- mutations (called from the dashboard) ---------------------------------

def set_runtime_config(
    mode: str, chain_id: int, rpcs: list[str],
    cmc_api_key: str = "", cmc_x402_base: str | None = None,
) -> dict:
    """Update the runtime config. Validates types. Returns the merged cfg.

    v2.1.1: writes to local.yaml (the user-state shadow), not the
    shipped config.yaml. The shipped file is treated as immutable at
    runtime; only `git pull` / a fresh clone can change it.
    """
    if mode not in ("testnet", "mainnet", "replay"):
        raise ValueError(f"invalid mode: {mode}")
    if chain_id not in (56, 97):
        raise ValueError(f"chain_id must be 56 (mainnet) or 97 (testnet), got {chain_id}")
    if not rpcs:
        raise ValueError("at least one RPC URL is required")
    for r in rpcs:
        if not r.startswith(("http://", "https://")):
            raise ValueError(f"invalid RPC URL: {r}")

    # Read the merged view (shipped + local) so we don't clobber any
    # existing local overrides (e.g. data_source.tier from a prior
    # wizard run). Then mutate the in-memory dict and write it back
    # as the new local.yaml.
    cfg = _load_merged_config()
    cfg["mode"] = mode
    cfg["chain_id"] = int(chain_id)
    cfg["rpcs"] = list(rpcs)
    cmc = cfg.setdefault("cmc", {})
    if cmc_api_key:
        cmc["api_key"] = cmc_api_key
    if cmc_x402_base:
        cmc["x402_base"] = cmc_x402_base
    write_local(cfg)
    return cfg


def generate_wallet(password: str) -> dict:
    """Create a new wallet, encrypt with `password`, persist keystore.

    The 12-word mnemonic is generated here (via Account.create()'s
    underlying bip32) and stored in the keystore so the dashboard's
    export-mnemonic endpoint can recover it later. The mnemonic is
    also returned in the response so the operator can write it down
    immediately on first create.
    """
    from eth_account import Account
    # Account.create() with no entropy_seed generates a fresh key but
    # does not expose the mnemonic. To capture the phrase we use the
    # mnemonic-based constructor with Account.generate_mnemonic().
    mnemonic = Account.generate_mnemonic()
    acct = Account.from_mnemonic(mnemonic)
    result = create_keystore(password, account=acct, mnemonic=mnemonic)
    result["mnemonic"] = mnemonic  # surface once to the operator
    return result


def import_wallet(private_key_hex: str, password: str) -> dict:
    """Import an existing private key, encrypt with `password`."""
    return import_keystore(private_key_hex, password)


def sign_current_policy(password: str) -> dict:
    """Unlock the keystore, sign policy.yaml with that key, write back.

    In single-user setups the evaluator == the agent address == the signer.
    In multi-sig setups, pre-set `evaluator_address` in policy.yaml to a
    different address before calling this function; we won't overwrite it.
    """
    from connectors.twak import TWAKWallet
    acct = unlock_and_get_account(password)
    path = Path("config/policy.yaml")
    if not path.exists():
        raise FileNotFoundError("config/policy.yaml missing — generate first")
    doc = yaml.safe_load(path.read_text())
    existing_eval = str(doc.get("evaluator_address", "") or "").strip()
    # setdefault is fine if it's already a real address
    if not existing_eval or existing_eval == "0x" + "00" * 20 or existing_eval == "0":
        doc["evaluator_address"] = acct.address
    doc["agent_address"] = acct.address
    wallet = TWAKWallet(address=acct.address, key=acct.key)
    sig = sign_policy(doc, wallet)
    doc["signature"] = sig
    _save_yaml(path, doc)
    return {
        "signature": sig,
        "evaluator_address": doc["evaluator_address"],
        "agent_address": acct.address,
        "version": doc.get("version"),
    }


def reset() -> dict:
    """Wipe operator state. Used by the 'Reset' button."""
    removed = []
    for p in [
        Path("config/policy.yaml"),
        Path("config/config.yaml"),
        _keystore_path(),
        SUMMARY_PATH,
    ]:
        if p.exists():
            p.unlink()
            removed.append(str(p))
    return {"removed": removed}


# --- env var helper --------------------------------------------------------

def export_env_for_process() -> dict:
    """Compute the env vars the agent should run with, given current state.

    Returned as a dict the dashboard can display. The actual env-mutation
    happens on the next agent start (bash bnbagent sources these).
    """
    state = load_setup_state()
    return {
        "TWAK_KEYSTORE":     state.keystore_path,
        "BNBAGENT_MODE":      state.mode,
        "CMC_API_KEY":        state.cmc_api_key,
    }
