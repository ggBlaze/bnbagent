"""x402 pay-per-request protocol — payment header build/parse + EIP-3009 signing."""
import base64
import json
import time
from decimal import Decimal

import pytest

from connectors.twak import TWAKWallet
from connectors.x402 import (
    decode_payment_requirements, build_x402_payment_sync, PaymentRequirements, X402Required,
)


EVALUATOR_KEY = "0x" + "a" * 64
USDC = "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d"


def make_requirements() -> PaymentRequirements:
    return PaymentRequirements(
        scheme="exact",
        network="bsc",
        token=USDC,
        amount=10_000,                # $0.01 in 6-dec USDC
        payTo="0x" + "f" * 40,
        nonce="nonce-12345",
        expiresAt=int(time.time()) + 60,
        extra={},
    )


def make_header_b64() -> str:
    req = make_requirements()
    payload = {
        "scheme":    req.scheme,
        "network":   req.network,
        "token":     req.token,
        "amount":    req.amount,
        "payTo":     req.payTo,
        "nonce":     req.nonce,
        "expiresAt": req.expiresAt,
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


class TestDecode:
    def test_decode_valid_header(self):
        b64 = make_header_b64()
        req = decode_payment_requirements(b64)
        assert req.scheme == "exact"
        assert req.network == "bsc"
        assert req.token == USDC
        assert req.amount == 10_000
        assert req.payTo == "0x" + "f" * 40

    def test_decode_empty_raises(self):
        with pytest.raises(X402Required):
            decode_payment_requirements("")

    def test_decode_malformed_raises(self):
        with pytest.raises(X402Required):
            decode_payment_requirements(base64.b64encode(b"not json").decode())


class TestBuildPayment:
    def test_build_payment_header(self):
        wallet = TWAKWallet.from_private_key(EVALUATOR_KEY)
        req = make_requirements()
        hdr = build_x402_payment_sync(wallet, req, chain_id=56)
        assert isinstance(hdr, str)
        # round-trip decode
        decoded = base64.b64decode(hdr)
        payload = json.loads(decoded)
        assert payload["x402Version"] == 1
        assert payload["scheme"] == "exact"
        assert payload["network"] == "bsc"
        sig = payload["payload"]["signature"]
        assert sig.startswith("0x")
        assert len(sig) == 132

    def test_signature_recovers_to_wallet(self):
        from eth_account import Account
        from eth_account.messages import encode_typed_data
        from web3 import Web3

        wallet = TWAKWallet.from_private_key(EVALUATOR_KEY)
        req = make_requirements()
        hdr = build_x402_payment_sync(wallet, req, chain_id=56)
        payload = json.loads(base64.b64decode(hdr))
        sig = payload["payload"]["signature"]
        auth = payload["payload"]["authorization"]
        # Recover the signer
        domain = {
            "name": "USD Coin", "version": "2",
            "chainId": 56, "verifyingContract": Web3.to_checksum_address(req.token),
        }
        types = {"TransferWithAuthorization": [
            {"name": "from",        "type": "address"},
            {"name": "to",          "type": "address"},
            {"name": "value",       "type": "uint256"},
            {"name": "validAfter",  "type": "uint256"},
            {"name": "validBefore", "type": "uint256"},
            {"name": "nonce",       "type": "bytes32"},
        ]}
        message = {
            "from":        Web3.to_checksum_address(wallet.address),
            "to":          Web3.to_checksum_address(req.payTo),
            "value":       int(auth["value"]),
            "validAfter":  int(auth["validAfter"]),
            "validBefore": int(auth["validBefore"]),
            "nonce":       auth["nonce"],
        }
        signable = encode_typed_data(domain, types, message)
        recovered = Account.recover_message(signable, signature=sig)
        assert recovered.lower() == wallet.address.lower()
