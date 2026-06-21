"""Boot sequence — load config, init wallet + connectors, register identity."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from decimal import Decimal

import jsonschema
import yaml

from connectors import BSCClient, PancakeV3, Perps, ERC8004, ERC8183, IPFSClient
from connectors.twak import TWAKWallet
from .portfolio import Portfolio
from policy.policy_verify import verify_policy
from .config_paths import load_config as _load_merged_config, write_local, DEFAULT_CONFIG

log = logging.getLogger(__name__)


def load_config(path: str = "config/config.yaml") -> dict:
    """Load the agent's runtime config.

    Backwards-compat shim: if `path` is the default `config/config.yaml`,
    use the local.yaml shadow pattern (merge `local.yaml` on top of
    `config.yaml`). Otherwise, read the explicit path verbatim (used
    by tests that want to point at a fixture file).
    """
    if Path(path) == DEFAULT_CONFIG:
        return _load_merged_config()
    return yaml.safe_load(open(path))


def load_policy(path: str = "config/policy.yaml") -> dict:
    """Read + schema-validate config/policy.yaml. If the file is
    missing (e.g. a fresh clone after `Reset Everything` wiped it),
    auto-generate a dev-signed policy from the shipped template so
    the agent can boot without manual intervention.

    The auto-generated policy uses an EPHEMERAL key — not the
    operator's wallet. The wizard's step 5 (Sign Policy) replaces
    this with the operator's real signed policy before the agent
    starts trading. This matches the install.sh fallback path
    (which also signs with a dev key on first run)."""
    p = Path(path)
    if not p.exists():
        log.warning("policy.yaml missing — generating dev-signed default; "
                    "the operator should re-sign via the wizard before live trading")
        from policy.policy_sign import _generate_dev_policy
        _generate_dev_policy(str(p), "config/config.yaml")
    doc = yaml.safe_load(open(path))
    schema = json.load(open("config/policy.schema.json"))
    jsonschema.validate(doc, schema)
    return doc


def init_wallet() -> TWAKWallet:
    w = TWAKWallet.from_env()
    log.info("wallet ready: %s", w.address)
    return w


def init_data_source(cfg: dict, wallet: TWAKWallet | None = None, replay_tape: list | None = None) -> "DataSourceRouter":
    """Construct the DataSourceRouter from config['data_source'].

    Default tier is 'mock' (replay/tests). The router's from_config factory
    picks CMCProClient / CMCX402Client / BinanceClient / MockClient based on
    the tier and the wallet/key availability.
    """
    from connectors.data_source import DataSourceRouter
    return DataSourceRouter.from_config(cfg, wallet=wallet)


def init_bsc(cfg: dict) -> dict:
    bsc = BSCClient(rpcs=cfg["rpcs"], chain_id=cfg["chain_id"], mode=cfg.get("mode", "testnet"))
    pancake = PancakeV3(
        client=bsc, router=cfg["dex"]["pcs_v3_router"],
        quoter=cfg["dex"]["pcs_v3_quoter"], factory=cfg["dex"]["pcs_v3_factory"],
    )
    perps = Perps(mode=cfg.get("mode", "testnet"))
    erc8004 = ERC8004(client=bsc, registry_address="0x" + "80" + "04" + "0" * 36)
    erc8183 = ERC8183(client=bsc, escrow_address="0x" + "81" + "83" + "0" * 36)
    return {"bsc": bsc, "pancake": pancake, "perps": perps, "erc8004": erc8004, "erc8183": erc8183}


def init_ipfs(cfg: dict) -> IPFSClient:
    return IPFSClient(
        api=os.environ.get("IPFS_API", "/ip4/127.0.0.1/tcp/5001"),
        mode=cfg.get("mode", "testnet"),
    )


def register_identity(erc8004: ERC8004, ipfs: IPFSClient, wallet: TWAKWallet,
                     policy: dict, version: str = "1.0.0") -> dict:
    """Build ERC-8004 metadata, pin to IPFS, register on-chain (or stub).

    v2.1.8 (#9): if a saved identity exists, reuse it ONLY when its
    `agent_address` matches the current wallet. Mismatch (operator
    imported a new wallet after a prior boot registered with an
    ephemeral one) re-registers so /api/identity reflects the wallet
    the operator actually controls. Same for a corrupt or
    pre-v2.1.8 file with no `agent_address` field.
    """
    identity_path = Path("~/.bnbagent/identity.json").expanduser()
    if identity_path.exists():
        try:
            saved = json.load(open(identity_path))
        except Exception as e:
            log.warning("identity.json corrupt (%s) — re-registering", e)
            saved = None
        if saved is not None:
            saved_addr = saved.get("agent_address")
            if saved_addr and saved_addr.lower() == wallet.address.lower():
                return saved
            if saved_addr:
                log.warning(
                    "identity.json was registered to %s but the current "
                    "wallet is %s — re-registering on-chain",
                    saved_addr, wallet.address,
                )
            else:
                log.warning(
                    "identity.json missing 'agent_address' (pre-v2.1.8?) — "
                    "re-registering to attach to wallet %s", wallet.address,
                )

    meta = {
        "name": "BNB Agent",
        "description": "Autonomous three-sleeve BSC trading agent: funding carry + DEX momentum + mean-reversion.",
        "image": "ipfs://Qm.../bnbagent.png",
        "attributes": [
            {"trait_type": "strategy",  "value": "three-sleeve-ensemble"},
            {"trait_type": "sleeves",   "value": ["A:funding-carry","B:dex-momentum","C:mean-reversion"]},
            {"trait_type": "chain",     "value": "bsc-mainnet" if policy.get("agent_address", "").startswith("0x4") else "bsc-testnet"},
            {"trait_type": "max_gross_leverage","value": policy["global_risk"]["max_gross_leverage"]},
            {"trait_type": "per_trade_risk","value": f"{policy['global_risk']['per_trade_risk_pct']}%"},
            {"trait_type": "daily_loss_cap","value": f"{policy['global_risk']['daily_loss_circuit_breaker_pct']}%"},
            {"trait_type": "version",   "value": version},
        ],
        "endpoints": {
            "metrics": "http://localhost:8000/metrics",
            "policy":  f"ipfs://placeholder/policy-{version}.yaml",
        },
        "trust": {
            "evaluator": policy["evaluator_address"],
            "operator":  wallet.address,
            "schema":    "erc-8004-v0",
        },
    }
    cid = ipfs.add_json(meta)
    token_id, _ = erc8004.register(agent_uri=f"ipfs://{cid}")
    identity = {
        "token_id": token_id,
        "cid": cid,
        "agent_address": wallet.address,
        "evaluator_address": policy["evaluator_address"],
        "version": version,
    }
    Path("~/.bnbagent").expanduser().mkdir(parents=True, exist_ok=True)
    json.dump(identity, open(Path("~/.bnbagent/identity.json").expanduser(), "w"), indent=2)
    log.info("ERC-8004 identity registered: tokenId=%s cid=%s", token_id, cid)
    return identity


def boot(starting_equity: Decimal = Decimal("100"),
         policy_path: str = "config/policy.yaml",
         config_path: str = "config/config.yaml",
         replay_tape: list | None = None,
         verify_signature: bool = False,
         mode: str | None = None,
         clock=None) -> dict:
    """Returns a dict of initialized components."""
    cfg = load_config(config_path)
    if mode is not None:
        cfg["mode"] = mode
    policy = load_policy(policy_path)

    if verify_signature:
        from policy.policy_verify import verify_policy as _verify
        if not _verify(policy, policy["evaluator_address"]):
            log.warning("policy signature does not recover to evaluator_address — proceeding in dev mode")

    wallet = init_wallet()
    policy["agent_address"] = wallet.address
    data_source = init_data_source(cfg, wallet=wallet, replay_tape=replay_tape)
    bs = init_bsc(cfg)
    ipfs = init_ipfs(cfg)

    # Plumb the Base address for x402. BSC and Base share the same
    # secp256k1 address format, so wallet.address IS the Base address.
    # The /api/data-source/x402-balance endpoint reads this from
    # config; the wizard's x402 step shows it as the funding target.
    # v2.1.8: always re-sync to wallet.address on every boot so a stale
    # or manually-edited value in local.yaml can't drift away from the
    # wallet the operator actually controls. Log a WARNING when the
    # value changes so the operator can spot the drift.
    # v2.1.1: write to local.yaml (the user-state shadow), not the
    # tracked config.yaml. See core/config_paths.py.
    try:
        ds_cfg = cfg.setdefault("data_source", {})
        prev = ds_cfg.get("base_address")
        if prev and prev.lower() != wallet.address.lower():
            log.warning(
                "data_source.base_address=%s does not match wallet.address=%s "
                "— re-syncing on boot. (Stale value? Wizard bug? Manual edit?)",
                prev, wallet.address,
            )
        ds_cfg["base_address"] = wallet.address
        write_local(cfg)
    except Exception as e:
        log.warning("could not write base_address to local.yaml: %s", e)
    # Deterministic clock (v2.0.4). In production this is wall clock;
    # in the replay harness it's set to a callable that returns the
    # current tape ts. The portfolio, perps, and sleeves all use the
    # same clock so the entire run is reproducible.
    portfolio = Portfolio(starting_equity=starting_equity, clock=clock)
    bs["perps"] = Perps(mode=cfg.get("mode", "testnet"), clock=clock)

    identity = register_identity(bs["erc8004"], ipfs, wallet, policy)

    log.info("BNB Agent booted: equity=$%s, address=%s, identity_token=%s",
             starting_equity, wallet.address, identity["token_id"])

    return {
        "config": cfg,
        "policy": policy,
        "wallet": wallet,
        "data_source": data_source,
        "bsc": bs["bsc"],
        "pancake": bs["pancake"],
        "perps": bs["perps"],
        "erc8004": bs["erc8004"],
        "erc8183": bs["erc8183"],
        "ipfs": ipfs,
        "portfolio": portfolio,
        "identity": identity,
    }
