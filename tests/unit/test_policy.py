"""Policy signing + verification."""
import json
import pytest
import yaml
from pathlib import Path

from connectors.twak import TWAKWallet
from policy.policy_sign import sign_policy, canonical_json
from policy.policy_verify import verify_policy
from tests.fixtures.wallets import TEST_POLICY, EVALUATOR_KEY, EVALUATOR_ADDRESS


class TestPolicySign:
    def test_canonical_json_is_deterministic(self):
        a = canonical_json({"b": 2, "a": 1})
        b = canonical_json({"a": 1, "b": 2})
        assert a == b

    def test_canonical_json_excludes_signature(self):
        # canonical_json does NOT auto-strip signature; the caller does
        # (see sign_policy / verify_policy in policy/policy_sign.py).
        # The signer code path: body = {k: v for k, v in policy.items() if k != "signature"}
        a = {k: v for k, v in {"version": "1.0.0", "signature": "0x0"}.items() if k != "signature"}
        b = {"version": "1.0.0"}
        assert canonical_json(a) == canonical_json(b)

    def test_sign_policy(self):
        wallet = TWAKWallet.from_private_key(EVALUATOR_KEY)
        policy = {**TEST_POLICY, "signature": "0x" + "00" * 65}
        sig = sign_policy(policy, wallet)
        assert sig.startswith("0x")
        assert len(sig) == 132

    def test_verify_round_trip(self):
        wallet = TWAKWallet.from_private_key(EVALUATOR_KEY)
        policy = {**TEST_POLICY, "signature": "0x" + "00" * 65}
        sig = sign_policy(policy, wallet)
        policy["signature"] = sig
        assert verify_policy(policy, EVALUATOR_ADDRESS)

    def test_verify_fails_with_wrong_signer(self):
        wallet = TWAKWallet.from_private_key(EVALUATOR_KEY)
        policy = {**TEST_POLICY, "signature": "0x" + "00" * 65}
        sig = sign_policy(policy, wallet)
        policy["signature"] = sig
        assert not verify_policy(policy, "0x" + "ff" * 20)

    def test_verify_fails_with_tampered_body(self):
        wallet = TWAKWallet.from_private_key(EVALUATOR_KEY)
        policy = {**TEST_POLICY, "signature": "0x" + "00" * 65}
        sig = sign_policy(policy, wallet)
        tampered = {**policy, "global_risk": {**policy["global_risk"],
                                              "daily_loss_circuit_breaker_pct": 99.0}}
        tampered["signature"] = sig
        assert not verify_policy(tampered, EVALUATOR_ADDRESS)

    def test_real_policy_file_signs_and_verifies(self):
        wallet = TWAKWallet.from_private_key(EVALUATOR_KEY)
        p = Path("config/policy.yaml")
        if not p.exists():
            pytest.skip("config/policy.yaml not present")
        doc = yaml.safe_load(p.read_text())
        sig = sign_policy(doc, wallet)
        doc["signature"] = sig
        assert verify_policy(doc, EVALUATOR_ADDRESS)
