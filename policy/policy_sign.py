"""Sign a policy.yaml file with TWAK EIP-191."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

from connectors.twak import TWAKWallet


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


def main():
    ap = argparse.ArgumentParser(description="Sign a BNB Agent policy.yaml with TWAK EIP-191.")
    ap.add_argument("--policy", default="config/policy.yaml")
    ap.add_argument("--out",    default=None, help="output path (default: in-place)")
    ap.add_argument("--pk",     default=None, help="private key (dev only)")
    args = ap.parse_args()

    if args.pk:
        wallet = TWAKWallet.from_private_key(args.pk)
    else:
        wallet = TWAKWallet.from_env()

    doc = sign_policy_file(args.policy, wallet)
    print(json.dumps({
        "version": doc.get("version"),
        "signature": doc.get("signature"),
        "evaluator_address": doc.get("evaluator_address"),
        "agent_address": doc.get("agent_address"),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
