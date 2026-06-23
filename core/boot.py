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


def load_config(path: str = "config/config.yaml", base_dir: Path | None = None) -> dict:
    """Load the agent's runtime config.

    Backwards-compat shim: if `path` is the default `config/config.yaml`,
    use the local.yaml shadow pattern (merge `local.yaml` on top of
    `config.yaml`). Otherwise, read the explicit path verbatim (used
    by tests that want to point at a fixture file).

    v2.2.0: `base_dir` is forwarded to `_load_merged_config()` so
    tests can pass tmp_path and not have the merged-load touch the
    operator's real config/local.yaml. The default (None) preserves
    the production behavior (Path.cwd()).
    """
    if Path(path) == DEFAULT_CONFIG:
        return _load_merged_config(base_dir=base_dir)
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
    # v2.3.0: switch from the placeholder to the canonical ERC-8004
    # IdentityRegistry at 0x8004A169FB4a3325136EB29fA0ceB6D2e539a432.
    # 8004scan.io (AltLayer's ERC-8004 explorer) only indexes this
    # contract's Transfer events on BSC mainnet — calling
    # register(string) here produces a real NFT that's visible at
    # https://www.8004scan.io/agents/bsc/{tokenId}. The previous
    # 0x212c61b... address is the BNB HACK 2026 CompetitionRegistry
    # (separate contract, tracks participation not identity).
    _ERC8004_IDENTITY_REGISTRY = "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"
    _BNB_HACK_2026_REGISTRY = "0x212c61b9b72c95d95bf29cf032f5e5635629aed5"
    # Wallet is wired later in init_wallet() / register_identity() —
    # pass None here so init_bsc doesn't depend on wallet order.
    erc8004 = ERC8004(client=bsc, registry_address=_ERC8004_IDENTITY_REGISTRY)
    erc8183 = ERC8183(client=bsc, escrow_address="0x" + "81" + "83" + "0" * 36)
    return {
        "bsc": bsc, "pancake": pancake, "perps": perps,
        "erc8004": erc8004, "erc8183": erc8183,
        # exposed for diagnostic logging
        "_identity_registry_canonical": _ERC8004_IDENTITY_REGISTRY,
        "_competition_registry": _BNB_HACK_2026_REGISTRY,
    }


def init_ipfs(cfg: dict) -> IPFSClient:
    return IPFSClient(
        api=os.environ.get("IPFS_API", "/ip4/127.0.0.1/tcp/5001"),
        mode=cfg.get("mode", "testnet"),
    )


def register_identity(erc8004: ERC8004, ipfs: IPFSClient, wallet: TWAKWallet,
                     policy: dict, version: str = "1.0.0") -> dict:
    """Build ERC-8004 metadata, pin to a public IPFS gateway, register on-chain.

    v2.3.0: pin the metadata to a public-resolvable gateway (Pinata if
    PINATA_API_KEY is set, else local IPFS daemon, else local-only CID)
    so 8004scan.io's crawler can HTTP-GET the agentURI from
    ``tokenURI(tokenId)`` and show real metadata on the agent page.
    The agentURI returned from ``pin_to_public_gateway`` is what we
    pass to the IdentityRegistry's ``register(string)``.

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
            {"trait_type": "hackathon", "value": "BNB-HACK-2026"},
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
    # v2.3.0: pin to a public gateway so 8004scan.io can fetch the metadata.
    cid, gateway_url = ipfs.pin_to_public_gateway(meta)
    log.info("identity pinned: cid=%s gateway_url=%s", cid, gateway_url)
    token_id, _ = erc8004.register(agent_uri=gateway_url)
    identity = {
        "token_id": token_id,
        "cid": cid,
        "agent_uri": gateway_url,
        "agent_address": wallet.address,
        "registry_address": erc8004.registry,
        "tx_hash": getattr(erc8004, "tx_hash", None),
        "evaluator_address": policy["evaluator_address"],
        "version": version,
    }
    Path("~/.bnbagent").expanduser().mkdir(parents=True, exist_ok=True)
    json.dump(identity, open(Path("~/.bnbagent/identity.json").expanduser(), "w"), indent=2)
    log.info(
        "ERC-8004 identity registered: tokenId=%s cid=%s registry=%s tx=%s",
        token_id, cid, erc8004.registry, identity["tx_hash"],
    )
    return identity


def boot(starting_equity: Decimal = Decimal("100"),
         policy_path: str = "config/policy.yaml",
         config_path: str = "config/config.yaml",
         replay_tape: list | None = None,
         verify_signature: bool = False,
         mode: str | None = None,
         clock=None,
         base_dir: Path | None = None) -> dict:
    """Returns a dict of initialized components.

    v2.2.0: `base_dir` is the directory containing `config/` (default
    Path.cwd() — the repo root in production). Tests must pass
    `base_dir=tmp_path` so the write_local() call below doesn't clobber
    the operator's real config/local.yaml with test fixture data. The
    2026-06-21 08:42 CST incident: a new test (test_boot_live_window_warn.py)
    called boot() without the _protect_real_local_yaml fixture, boot()
    wrote the test's stub (test wallet 0x81B24, test DEX 0x1111..., localhost
    RPC) back to the real local.yaml, and the agent started in replay
    mode against the test fixtures. Tests now pass base_dir explicitly."""
    from pathlib import Path as _P
    base_dir = _P(base_dir) if base_dir else None
    cfg = load_config(config_path, base_dir=base_dir)
    if mode is not None:
        cfg["mode"] = mode
    policy = load_policy(policy_path)

    if verify_signature:
        from policy.policy_verify import verify_policy as _verify
        if not _verify(policy, policy["evaluator_address"]):
            log.warning("policy signature does not recover to evaluator_address — proceeding in dev mode")

    # v2.2.0: warn if running in testnet/live mode without a live_window
    # gate. The gate code in core/risk.py is backward compatible (missing
    # fields = no gate), which is correct for backtests/paper, but a
    # production deployment that forgets to set the window is a foot-gun:
    # a missed kill switch can pre-trade. Surface the missing-config
    # case in the boot log so it's visible in the journal.
    if cfg.get("mode") in ("testnet", "live", "mainnet"):
        gr = policy.get("global_risk", {})
        if "live_window_start" not in gr or not gr.get("live_window_start"):
            log.warning(
                "running in %s mode WITHOUT a live_window_start gate. "
                "Trades will not be blocked by a time window — "
                "the only protection against pre-window trading is "
                "the kill switch. Set policy.global_risk.live_window_start "
                "and live_window_end to harden (see config/policy.yaml.example).",
                cfg.get("mode"),
            )

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
        write_local(cfg, base_dir=base_dir)
    except Exception as e:
        log.warning("could not write base_address to local.yaml: %s", e)
    # Deterministic clock (v2.0.4). In production this is wall clock;
    # in the replay harness it's set to a callable that returns the
    # current tape ts. The portfolio, perps, and sleeves all use the
    # same clock so the entire run is reproducible.
    portfolio = Portfolio(starting_equity=starting_equity, clock=clock)
    # v2.1.8: perps mark cache TTL (seconds). Defaults to 60s so the
    # sleeve-A 30s tick hits a fresh fetch every other tick. Operators
    # can override in policy.yaml under `perps.mark_cache_ttl_s`.
    mark_cache_ttl_s = int(
        (cfg.get("perps") or {}).get("mark_cache_ttl_s", 60)
    )
    bs["perps"] = Perps(
        mode=cfg.get("mode", "testnet"),
        clock=clock,
        mark_cache_ttl_s=mark_cache_ttl_s,
    )

    # v2.3.0: wire the wallet into ERC8004 BEFORE register_identity runs.
    # Mainnet register() needs the wallet to sign the on-chain tx; without
    # this it would raise. Max gas cap comes from config (default 5 gwei —
    # well above BSC's 0.05 gwei floor so the tx always lands, but capped
    # so a stuck-tx can't burn through the $15 BNB stack on gas spikes).
    max_gas_price_gwei = float((cfg.get("gas") or {}).get("max_gwei", 5.0))
    bs["erc8004"].wallet = wallet
    bs["erc8004"].max_gas_price_gwei = max_gas_price_gwei
    log.info(
        "ERC8004 wired: registry=%s wallet=%s max_gas=%s gwei",
        bs["erc8004"].registry, wallet.address, max_gas_price_gwei,
    )

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
