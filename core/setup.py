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


def _persist_base_address_if_unset(address: str, *, base_dir: Path | None = None) -> None:
    """Write the wallet's address to `config/local.yaml` under
    `data_source.base_address` so the wizard's x402 step + the
    /api/data-source/x402-balance endpoint can find it.

    Mirrors the same write in core/boot.py. The wizard calls
    `import_wallet` / `generate_wallet` BEFORE the agent boots, so
    without this write `data_source.base_address` stays at whatever
    the previous boot wrote (which may be an ephemeral key from a
    prior session, or the placeholder from local.yaml.example), and
    the data-source step + x402 balance polling would target the
    wrong address.

    Always writes on import/generate — the freshly-imported/gener
    wallet's address is by definition the operator's current intent.
    If the operator wants to poll a DIFFERENT address's USDC balance
    for x402 funding (advanced multi-sig scenario), they can set it
    by editing local.yaml after import; the wizard path always wins
    here.

    v2.1.8: added `base_dir` kwarg so tests can scope the read+write
    to a tmp dir. Production callers (import_wallet, generate_wallet)
    don't pass it; they want the cwd-relative write into the
    user's real config/local.yaml.
    """
    try:
        cfg = _load_merged_config(base_dir=base_dir)
        ds = cfg.setdefault("data_source", {})
        ds["base_address"] = Web3.to_checksum_address(address)
        write_local(cfg, base_dir=base_dir)
    except Exception as e:
        # Persistence is best-effort here. The wallet itself was
        # already encrypted to disk by create_keystore / import_keystore;
        # if the config write fails the operator can fix local.yaml by
        # hand and the next boot() call will retry.
        import logging
        logging.getLogger(__name__).warning(
            "could not persist data_source.base_address: %s", e
        )


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
    _persist_base_address_if_unset(result["address"])
    return result


def import_wallet(private_key_hex: str, password: str) -> dict:
    """Import an existing private key, encrypt with `password`."""
    result = import_keystore(private_key_hex, password)
    _persist_base_address_if_unset(result["address"])
    # v2.1.8 (C): auto-write TWAK_KEYSTORE=~/.twak/wallet.json to
    # .env so the next `bash bnbagent` boot's core.main process can
    # find the keystore via TWAKWallet.from_env(). Without this the
    # dashboard sees the right wallet but the agent loop falls
    # through to the ephemeral dev key (from_env warning line),
    # signing with a different address than the operator thinks,
    # and syncing data_source.base_address to the wrong key.
    # The dashboard /api/setup endpoint still respects the operator
    # if they want a non-default path — they can edit .env directly
    # and we'll never overwrite an existing TWAK_KEYSTORE value.
    try:
        from dashboard.backend.main import (
            _set_env_var_in_dotenv as _dash_setenv,
        )
        import os
        # Only set if not already present in os.environ (the
        # operator may have set it to a non-default path).
        if not os.environ.get("TWAK_KEYSTORE"):
            ks_path = str(Path.home() / ".twak" / "wallet.json")
            _dash_setenv("TWAK_KEYSTORE", ks_path)
            os.environ["TWAK_KEYSTORE"] = ks_path
    except Exception:
        # dashboard import may not be available in some test contexts;
        # the keystore on disk is still importable via unlock_and_get_account()
        # which uses connectors/keystore.py's path resolution. Silent
        # fallback is fine here.
        pass
    return result


def sign_current_policy(password: str) -> dict:
    """Unlock the keystore, sign policy.yaml with that key, write back.

    The signer is ALWAYS the unlocked wallet. We overwrite both
    `evaluator_address` and `agent_address` to match the wallet so
    the signature recovers cleanly. The earlier "preserve the
    existing evaluator" branch was wrong: a policy that was signed
    with the dev key during `bash install.sh` keeps the dev-key
    evaluator address on disk, and the operator's import + re-sign
    then produces a signature that doesn't recover to the policy's
    claimed evaluator → `verify=INVALID` → the on-chain registration
    gets rejected. Hit this in BNB HACK 2026 prep.

    Multi-sig setups (a separate evaluator key that doesn't match
    the agent's signer) need to bypass this function and use
    `policy.sign_policy_file()` directly with their own key.

    If config/policy.yaml doesn't exist, we generate it from the
    shipped DEFAULT_POLICY_BODY template with the unlocked wallet
    as both evaluator and agent.
    """
    from connectors.twak import TWAKWallet
    import time
    from policy.policy_sign import DEFAULT_POLICY_BODY, sign_policy as _sign_policy
    from policy.policy_verify import verify_policy
    acct = unlock_and_get_account(password)
    path = Path("config/policy.yaml")
    if not path.exists():
        # Fresh-install case. Generate a clean template with the
        # unlocked wallet as both evaluator and agent.
        now = int(time.time())
        evaluator = Web3.to_checksum_address(acct.address)
        agent = evaluator
        body_yaml = DEFAULT_POLICY_BODY.replace("__ISSUED__", str(now))
        body_yaml = body_yaml.replace("__EXPIRES__", str(now + 30 * 24 * 3600))
        body_yaml = body_yaml.replace("__EVAL__", evaluator)
        body_yaml = body_yaml.replace("__AGENT__", agent)
        body_yaml = body_yaml.replace("__SIG__", "0x" + "00" * 65)
        doc = yaml.safe_load(body_yaml)
    else:
        doc = yaml.safe_load(path.read_text())
    # v2.1.8 (B): always align the on-disk evaluator + agent with the
    # signer. The Sign function only has access to the unlocked
    # wallet's key, so a "preserve existing evaluator" branch would
    # produce a signature that doesn't recover to the claimed
    # evaluator and break verify_policy. Multi-sig setups must
    # bypass this function (see docstring).
    doc["evaluator_address"] = Web3.to_checksum_address(acct.address)
    doc["agent_address"] = Web3.to_checksum_address(acct.address)
    wallet = TWAKWallet(address=acct.address, key=acct.key)
    sig = _sign_policy(doc, wallet)
    doc["signature"] = sig
    # v2.1.8 (B): defensive post-sign verification. If the signature
    # doesn't recover to the evaluator we just wrote, refuse to save
    # and raise. Catches any future key/format drift before it lands
    # on disk as an INVALID policy.
    if not verify_policy(doc, acct.address):
        raise RuntimeError(
            f"sign_current_policy: signature {sig[:18]}... does not recover "
            f"to evaluator_address {acct.address}. Policy NOT saved. "
            f"This should be impossible — open an issue."
        )
    _save_yaml(path, doc)
    return {
        "signature": sig,
        "evaluator_address": doc["evaluator_address"],
        "agent_address": acct.address,
        "version": doc.get("version"),
    }


def reset(include_wallet: bool = False) -> dict:
    """Wipe operator state. Used by the 'Reset' button.

    Wipes only gitignored STATE files:
      - config/policy.yaml  (operator-signed, has their key)
      - config/local.yaml   (operator overrides)
      - ~/.bnbagent/setup.json (cached summary for the dashboard)

    v2.1.8 (P7): by default, KEEPS the encrypted wallet keystore at
    ~/.twak/wallet.json. The operator imported it deliberately; a
    config reset shouldn't take their wallet with it (forcing a
    re-import for what they probably intended as just a config tweak).
    Pass `include_wallet=True` to also delete the keystore — used for
    wallet rotation, hand-off to another operator, or a truly-
    everything wipe.

    Does NOT touch config/config.yaml — that's a TRACKED file
    with the shipped defaults (RPCs, base_rpcs, base_address
    schema, etc). Wiping it broke x402 balance polling in v2.1.5+
    because the default base_rpcs list only lives in that file;
    without it /api/data-source/x402-balance returns 422
    "no base_rpcs configured".
    """
    removed = []
    targets = [
        Path("config/policy.yaml"),
        Path("config/local.yaml"),
        SUMMARY_PATH,
    ]
    if include_wallet:
        targets.insert(2, _keystore_path())
    for p in targets:
        if p.exists():
            p.unlink()
            removed.append(str(p))
    return {"removed": removed, "wallet_kept": not include_wallet}


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
