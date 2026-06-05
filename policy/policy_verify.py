"""Verify a policy.yaml signature recovers to the evaluator_address."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

from .policy_sign import canonical_json

log = logging.getLogger(__name__)


def verify_policy(policy: dict, expected_signer: str) -> bool:
    """Recover signer of `policy['signature']` over the policy body. Return True if it matches."""
    if "signature" not in policy:
        log.error("policy has no signature field")
        return False
    sig = policy["signature"]
    if not sig.startswith("0x") or len(sig) != 132:
        log.error("policy signature has wrong length: %d", len(sig))
        return False
    body = {k: v for k, v in policy.items() if k != "signature"}
    digest = Web3.keccak(canonical_json(body))
    try:
        # IMPORTANT: must use primitive= with raw bytes to match the signer
        # (TWAKWallet.sign_message_eip191 passes a 0x-prefixed hex → bytes).
        raw = Web3.to_bytes(hexstr="0x" + digest.hex())
        signable = encode_defunct(primitive=raw)
        recovered = Account.recover_message(signable, signature=sig)
    except Exception as e:
        log.error("signature recovery failed: %s", e)
        return False
    return recovered.lower() == expected_signer.lower()


def verify_policy_file(path: str, expected_signer: str | None = None) -> bool:
    p = Path(path)
    doc = yaml.safe_load(p.read_text())
    if expected_signer is None:
        expected_signer = doc.get("evaluator_address", "")
    return verify_policy(doc, expected_signer)


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "config/policy.yaml"
    ok = verify_policy_file(path)
    print("VERIFIED" if ok else "INVALID")
    sys.exit(0 if ok else 1)
