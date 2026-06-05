"""x402 pay-per-request protocol.

Flow:
  1. Client → Server: HTTP request.
  2. Server → Client: 402 + X-PAYMENT-REQUIRED header (base64 JSON of payment reqs).
  3. Client signs EIP-3009 transferWithAuthorization over USDC.
  4. Client → Server: retry with X-PAYMENT header.
  5. Server → Client: 200.

USDC contract on BSC mainnet: 0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d (native Circle, EIP-3009)
USDC contract on BSC testnet: see config/config.yaml.

This module is a self-contained reimplementation that does NOT require the upstream
x402 SDK. It signs EIP-3009 transferWithAuthorization messages using eth_account.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

log = logging.getLogger(__name__)

# EIP-712 domain + types for USDC EIP-3009 transferWithAuthorization
USDC_EIP712_DOMAIN = {
    "name": "USD Coin",
    "version": "2",
}

USDC_TRANSFER_WITH_AUTH_TYPES = {
    "TransferWithAuthorization": [
        {"name": "from",        "type": "address"},
        {"name": "to",          "type": "address"},
        {"name": "value",       "type": "uint256"},
        {"name": "validAfter",  "type": "uint256"},
        {"name": "validBefore", "type": "uint256"},
        {"name": "nonce",       "type": "bytes32"},
    ]
}


class X402Required(Exception):
    pass


@dataclass
class PaymentRequirements:
    scheme: str
    network: str
    token: str
    amount: int                # in token's smallest unit
    payTo: str
    nonce: str
    expiresAt: int
    extra: dict[str, Any]


def decode_payment_requirements(b64_header: str) -> PaymentRequirements:
    if not b64_header:
        raise X402Required("missing X-PAYMENT-REQUIRED header")
    try:
        raw = base64.b64decode(b64_header)
        d = json.loads(raw)
    except Exception as e:
        raise X402Required(f"malformed payment header: {e}") from e
    return PaymentRequirements(
        scheme=d.get("scheme", "exact"),
        network=d.get("network", "bsc"),
        token=d.get("token", d.get("asset", "")),
        amount=int(d.get("amount", d.get("maxAmountRequired", 0))),
        payTo=d.get("payTo", d.get("payToAddress", "")),
        nonce=d.get("nonce", ""),
        expiresAt=int(d.get("expiresAt", d.get("validBefore", time.time() + 60))),
        extra={k: v for k, v in d.items() if k not in
               {"scheme", "network", "token", "asset", "amount",
                "maxAmountRequired", "payTo", "payToAddress", "nonce", "expiresAt", "validBefore"}},
    )


def _eip3009_nonce(s: str) -> bytes:
    return Web3.keccak(text=s) if not s.startswith("0x") else bytes.fromhex(s[2:])


async def x402_pay(required_b64: str, wallet, chain_id: int = 56) -> str:
    """Build an X-PAYMENT header value (base64) that satisfies the 402.

    The wallet must expose:
      - wallet.address     : str (0x...)
      - wallet.sign_typed_data(domain, types, value) -> signed (eth_account style)
    """
    req = decode_payment_requirements(required_b64)
    log.info(
        "x402 pay: scheme=%s network=%s token=%s amount=%d payTo=%s nonce=%s",
        req.scheme, req.network, req.token, req.amount, req.payTo, req.nonce,
    )

    if req.scheme != "exact":
        raise X402Required(f"unsupported scheme: {req.scheme}")
    if req.network != "bsc":
        raise X402Required(f"unsupported network: {req.network}")
    if req.amount <= 0:
        raise X402Required("zero amount in payment requirements")

    domain = {
        **USDC_EIP712_DOMAIN,
        "chainId": chain_id,
        "verifyingContract": Web3.to_checksum_address(req.token),
    }
    message = {
        "from":        Web3.to_checksum_address(wallet.address),
        "to":          Web3.to_checksum_address(req.payTo),
        "value":       req.amount,
        "validAfter":  int(time.time()) - 60,
        "validBefore": req.expiresAt,
        "nonce":       "0x" + _eip3009_nonce(req.nonce).hex(),
    }
    signable = encode_typed_data(domain, USDC_TRANSFER_WITH_AUTH_TYPES, message)
    signed = Account.sign_message(signable, wallet.key)

    payload = {
        "x402Version": 1,
        "scheme":      req.scheme,
        "network":     req.network,
        "payload": {
            "signature": "0x" + signed.signature.hex(),
            "authorization": {
                **message,
                "from":  message["from"],
                "to":    message["to"],
                "value": str(message["value"]),
            },
        },
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


def build_x402_payment_sync(wallet, req: PaymentRequirements, chain_id: int = 56) -> str:
    """Synchronous version for tests + replay harness."""
    domain = {
        **USDC_EIP712_DOMAIN,
        "chainId": chain_id,
        "verifyingContract": Web3.to_checksum_address(req.token),
    }
    message = {
        "from":        Web3.to_checksum_address(wallet.address),
        "to":          Web3.to_checksum_address(req.payTo),
        "value":       req.amount,
        "validAfter":  int(time.time()) - 60,
        "validBefore": req.expiresAt,
        "nonce":       "0x" + _eip3009_nonce(req.nonce).hex(),
    }
    signable = encode_typed_data(domain, USDC_TRANSFER_WITH_AUTH_TYPES, message)
    signed = Account.sign_message(signable, wallet.key)
    payload = {
        "x402Version": 1,
        "scheme":      req.scheme,
        "network":     req.network,
        "payload": {
            "signature": "0x" + signed.signature.hex(),
            "authorization": {
                **message,
                "value": str(message["value"]),
            },
        },
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()
