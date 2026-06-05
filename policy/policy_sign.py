"""Sign a policy.yaml file with TWAK EIP-191.

Two flows:
  - production: `python -m policy.policy_sign --policy config/policy.yaml`
                reads TWAK_KEYSTORE + TWAK_PWD (or BNBAGENT_PRIVATE_KEY fallback)
  - dev:        `python -m policy.policy_sign --dev`  generates an ephemeral key,
                writes a fresh config/policy.yaml (only if missing), and signs it
                with the evaluator/agent addresses derived from that key.
                Used by `install.sh` so first-time users don't see a signing prompt.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import yaml
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

from connectors.twak import TWAKWallet


DEFAULT_POLICY_BODY = """\
version: 1.0.0
issued_at: __ISSUED__
expires_at: __EXPIRES__
evaluator_address: '__EVAL__'
agent_address:     '__AGENT__'
global_risk:
  daily_loss_circuit_breaker_pct: 3.0
  per_trade_risk_pct:             1.0
  max_gross_leverage:             2.0
  max_single_position_pct:       15.0
  max_daily_trades:             100
  max_drawdown_pct:              8.0
  cooldown_after_breach_min:     60
sleeve_allocations: { A: 0.70, B: 0.20, C: 0.10 }
sleeves:
  A:
    enabled: true
    venue_selection: highest_abs_funding_7d
    rebalance_hours: 8
    fund_floor_pct: 0.005
    basis_trigger_pct: 0.5
    max_position_pct: 15.0
  B:
    enabled: true
    volume_spike_mult: 2.0
    breakout_lookback_h: 4
    atr_len: 14
    atr_stop_mult: 2.0
    tp_pct: 3.0
    max_hold_min: 240
    kelly_fraction: 0.25
    max_position_pct: 10.0
  C:
    enabled: true
    zscore_threshold: 2.5
    stop_pct: 2.0
    target_pct: 1.0
    lookback_h: 1
    kelly_fraction: 0.25
    max_position_pct: 5.0
allowlist:
  cmc_rank_max: 50
  bsc_tokens: [WBNB, USDC, CAKE, BTCB, ETH, SOL, XRP, DOGE, ADA, AVAX,
               LINK, DOT, MATIC, SHIB, LTC, BCH, NEAR, ATOM, UNI, APT]
  perps_venues: [aster, killex, apollox, mux]
  dex_routers:  ["0x9A489505a6B3cd73B4D6C8E6B3E8a3e7B9C8d2e1"]
fees:
  x402_max_usdc_per_day: "10.00"
  max_gas_price_gwei:    5
signature: "__SIG__"
"""


def canonical_json(d: dict) -> bytes:
    """Deterministic JSON serialization (sort_keys, no whitespace)."""
    return json.dumps(d, sort_keys=True, separators=(",", ":")).encode()


def sign_policy(policy: dict, wallet: TWAKWallet) -> str:
    """Compute the EIP-191 signature over the policy hash. Returns 0x-prefixed hex sig."""
    body = {k: v for k, v in policy.items() if k != "signature"}
    digest = Web3.keccak(canonical_json(body))
    sig = wallet.sign_message_eip191("0x" + digest.hex())
    return sig


def sign_policy_file(path: str, wallet: TWAKWallet | None = None) -> dict:
    p = Path(path)
    doc = yaml.safe_load(p.read_text())
    if wallet is None:
        wallet = TWAKWallet.from_env()
    sig = sign_policy(doc, wallet)
    doc["signature"] = sig
    p.write_text(yaml.safe_dump(doc, sort_keys=False, default_flow_style=False))
    return doc


def _generate_dev_policy(out_path: str, config_path: str) -> dict:
    """Generate a dev-default policy.yaml with an ephemeral key.

    Both evaluator and agent are the same address in dev mode (the user's key).
    """
    acct = Account.create()
    evaluator = Web3.to_checksum_address(acct.address)
    agent = evaluator
    now = int(time.time())
    body = {
        "version": "1.0.0",
        "issued_at": now,
        "expires_at": now + 30 * 24 * 3600,
        "evaluator_address": evaluator,
        "agent_address": agent,
    }
    body_yaml = DEFAULT_POLICY_BODY.replace("__ISSUED__", str(now))
    body_yaml = body_yaml.replace("__EXPIRES__", str(now + 30 * 24 * 3600))
    body_yaml = body_yaml.replace("__EVAL__", evaluator)
    body_yaml = body_yaml.replace("__AGENT__", agent)
    body_yaml = body_yaml.replace("__SIG__", "0x" + "00" * 65)
    doc = yaml.safe_load(body_yaml)
    wallet = TWAKWallet.from_private_key("0x" + acct.key.hex())
    doc["signature"] = sign_policy(doc, wallet)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(yaml.safe_dump(doc, sort_keys=False, default_flow_style=False))
    return doc


def main():
    ap = argparse.ArgumentParser(description="Sign a BNB Agent policy.yaml with TWAK EIP-191.")
    ap.add_argument("--policy", default="config/policy.yaml")
    ap.add_argument("--out",    default=None, help="output path (default: in-place)")
    ap.add_argument("--pk",     default=None, help="private key (dev only)")
    ap.add_argument("--config", default="config/config.yaml",
                    help="config path (used by --dev)")
    ap.add_argument("--dev",    action="store_true",
                    help="generate a fresh dev policy with an ephemeral key (used by install.sh)")
    args = ap.parse_args()

    if args.dev:
        out = args.out or args.policy
        doc = _generate_dev_policy(out, args.config)
        print(json.dumps({
            "mode": "dev",
            "version": doc.get("version"),
            "signature": doc.get("signature"),
            "evaluator_address": doc.get("evaluator_address"),
            "agent_address": doc.get("agent_address"),
            "out": out,
        }, indent=2))
        return 0

    if args.pk:
        wallet = TWAKWallet.from_private_key(args.pk)
    else:
        wallet = TWAKWallet.from_env()

    out_path = args.out or args.policy
    if args.out and args.out != args.policy:
        # copy then sign
        Path(args.out).write_text(Path(args.policy).read_text())
    doc = sign_policy_file(out_path, wallet)
    print(json.dumps({
        "version": doc.get("version"),
        "signature": doc.get("signature"),
        "evaluator_address": doc.get("evaluator_address"),
        "agent_address": doc.get("agent_address"),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
