"""x402 pay-per-request protocol.

Flow (x402 exact-EVM scheme per
https://github.com/coinbase/x402/blob/main/specs/schemes/exact/scheme_exact_evm.md):

  1. Client → Server: HTTP request.
  2. Server → Client: 402 + PAYMENT-REQUIRED header (base64 JSON of payment reqs).
  3. Client signs EIP-3009 transferWithAuthorization over USDC.
  4. Client → Server: retry with PAYMENT-SIGNATURE header.
  5. Server → Client: 200.

CMC's x402 facilitator settles on **Base mainnet** (chain 8453) with native
USDC at 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913 (EIP-3009-capable).

This module is a self-contained reimplementation that does NOT require the upstream
x402 SDK. It signs EIP-3009 transferWithAuthorization messages using eth_account.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3
# Module-level alias so tests can mock `connectors.x402._W3`.
_W3 = Web3

log = logging.getLogger(__name__)

# Default settlement: Base mainnet (chain 8453), native USDC.
DEFAULT_CHAIN_ID = 8453
DEFAULT_TOKEN_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
DEFAULT_BASE_RPCS = [
    "https://mainnet.base.org",
    "https://base.publicnode.com",
    "https://1rpc.io/base",
]
DEFAULT_BSC_RPCS = [
    "https://bsc-dataseed.binance.org",
    "https://bsc-dataseed1.defibit.io",
    "https://bsc-dataseed1.ninicoin.io",
]
# v2.2.2: per-chain RPC map for pick_payable_accept. The agent may be
# asked to pay on either BSC (eip155:56) or Base (eip155:8453) and
# needs to query the right RPC to check the wallet's balance.
_RPC_BY_CHAIN = {
    56:  DEFAULT_BSC_RPCS[0],
    8453: DEFAULT_BASE_RPCS[0],
}


def _default_chain_id() -> int:
    return DEFAULT_CHAIN_ID


def _default_token_address() -> str:
    return DEFAULT_TOKEN_ADDRESS


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
    resource: dict[str, Any] | None = None  # v2.2.2: x402 V2 resource field


def decode_payment_requirements(b64_header: str) -> PaymentRequirements:
    if not b64_header:
        raise X402Required("missing PAYMENT-REQUIRED header")
    try:
        raw = base64.b64decode(b64_header)
        d = json.loads(raw)
    except Exception as e:
        raise X402Required(f"malformed payment header: {e}") from e

    # v2.1.8 (F4): handle the canonical x402 envelope shape per
    # github.com/coinbase/x402/.../x402Specs.ts. The 402 body is:
    #   {"x402Version": 1, "accepts": [<PaymentRequirements>, ...]}
    # We pick the first accept (matching the TS SDK's
    # selectPaymentRequirements default). The pre-canonical flat-dict
    # shape used by our own test fixtures still works because we fall
    # through to the same key reads on `d` itself.
    accepts = d.get("accepts")
    # v2.2.2: capture the resource (x402 V2 requires it echoed back in
    # the PAYMENT-SIGNATURE payload).
    resource = d.get("resource")
    if isinstance(accepts, list):
        if not accepts:
            raise X402Required("empty 'accepts' array in payment requirements")
        d = accepts[0]
        if not isinstance(d, dict):
            raise X402Required("first 'accepts' entry is not an object")

    # v2.1.8 (F4): maxAmountRequired arrives as a STRING per the spec
    # (z.string().refine(isInteger)). int() handles both cases.
    raw_amount = d.get("amount", d.get("maxAmountRequired", 0))
    try:
        amount = int(raw_amount)
    except (TypeError, ValueError) as e:
        raise X402Required(f"malformed amount {raw_amount!r}: {e}") from e

    # v2.1.8 (F4): `maxTimeoutSeconds` is RELATIVE; `expiresAt`/`validBefore`
    # are ABSOLUTE. Prefer absolute when present (older callers + tests),
    # otherwise synthesize `now + maxTimeoutSeconds`. Fall back to 60s if
    # neither is provided (matches the prior default).
    if "expiresAt" in d:
        expires_at = int(d["expiresAt"])
    elif "validBefore" in d:
        expires_at = int(d["validBefore"])
    elif "maxTimeoutSeconds" in d:
        expires_at = int(time.time()) + int(d["maxTimeoutSeconds"])
    else:
        expires_at = int(time.time()) + 60

    return PaymentRequirements(
        scheme=d.get("scheme", "exact"),
        network=d.get("network", "bsc"),
        token=d.get("token", d.get("asset", "")),
        amount=amount,
        payTo=d.get("payTo", d.get("payToAddress", "")),
        nonce=d.get("nonce", ""),
        expiresAt=expires_at,
        extra=dict(d.get("extra") or {}) if isinstance(d.get("extra"), dict) else {
            k: v for k, v in d.items() if k not in
            {"scheme", "network", "token", "asset", "amount",
             "maxAmountRequired", "payTo", "payToAddress", "nonce",
             "expiresAt", "validBefore", "maxTimeoutSeconds"}
        },
        resource=resource,
    )


def _eip3009_nonce(s: str) -> bytes:
    return Web3.keccak(text=s) if not s.startswith("0x") else bytes.fromhex(s[2:])


# v2.2.2: per-accept balance check so the agent picks the token+chain
# it can actually pay. CMC's x402 endpoint offers ~7 accepts on a
# typical request: 6 on BSC (USDC, USDT, plus 4 alt-stables the agent
# doesn't hold) and 1 on Base (USDC). Without filtering, the client
# picks accepts[0] which is usually a BSC alt-stable the agent has
# 0 of, and the signed authorization settles against an empty balance
# so the server returns 402 again forever. The fix is to query the
# agent's balance for each accept and pick the first one with
# sufficient balance. The cost is one extra eth_call per accept
# (~7 RPCs per 402), well within the agent's per-tick budget.

_ERC20_BALANCE_OF_ABI = [
    {"inputs": [{"name": "_owner", "type": "address"}],
     "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
]


def _parse_chain_id(network: str) -> int | None:
    """Map an x402 `network` field to a numeric chain id.

    Accepts "eip155:<n>" (canonical), "bsc"/"base" (legacy), and None.
    Returns None if the network is unrecognised.
    """
    if not network:
        return None
    if network.startswith("eip155:"):
        try:
            return int(network.split(":", 1)[1])
        except ValueError:
            return None
    if network == "bsc":
        return 56
    if network == "base":
        return 8453
    return None


def _rpc_for_chain(chain_id: int) -> str | None:
    return _RPC_BY_CHAIN.get(chain_id)


async def pick_payable_accept(accepts: list[dict], wallet_address: str) -> dict | None:
    """Iterate the 402 accepts in order; return the first one whose
    token the wallet actually holds enough of to cover `amount`.

    Returns None if no accept is payable. Cheap reject: if the
    accept's amount is 0 or the chain is unknown, skip.
    """
    for a in accepts:
        if not isinstance(a, dict):
            continue
        chain_id = _parse_chain_id(a.get("network", ""))
        if chain_id is None:
            continue
        token = a.get("asset") or a.get("token") or ""
        if not token:
            continue
        amount_str = a.get("amount") or a.get("maxAmountRequired") or "0"
        try:
            amount = int(amount_str)
        except (TypeError, ValueError):
            continue
        if amount <= 0:
            continue
        rpc = _rpc_for_chain(chain_id)
        if not rpc:
            continue
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 5}))
            token_c = w3.eth.contract(
                address=Web3.to_checksum_address(token),
                abi=_ERC20_BALANCE_OF_ABI,
            )
            bal = token_c.functions.balanceOf(
                Web3.to_checksum_address(wallet_address)
            ).call()
        except Exception:
            continue
        if bal >= amount:
            return a
    return None



async def x402_pay(
    required_b64: str,
    wallet,
    chain_id: int = DEFAULT_CHAIN_ID,
    token_address: str = DEFAULT_TOKEN_ADDRESS,
) -> str:
    """Build a PAYMENT-SIGNATURE header value (base64) that satisfies the 402.

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
    if req.network not in ("bsc", "eip155:56", "eip155:8453"):
        raise X402Required(f"unsupported network: {req.network}")
    if req.amount <= 0:
        raise X402Required("zero amount in payment requirements")

    domain = {
        **USDC_EIP712_DOMAIN,
        "chainId": chain_id,
        "verifyingContract": Web3.to_checksum_address(token_address),
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
    # v2.2.2: TWAKWallet.sign_typed_data already returns the 0x-prefixed
    # hex signature string; calling Account.sign_message(signable, wallet.key)
    # would fail because the wallet object exposes a sign_typed_data method
    # (not the raw `.key` bytes — and even when `.key` is set, the wallet
    # has a different sign_transaction / sign_typed_data path that we want
    # to reuse for consistency with the rest of the agent's signing).
    signature_hex = wallet.sign_typed_data(domain, USDC_TRANSFER_WITH_AUTH_TYPES, message)

    # v2.2.2: x402 V2 spec requires the chosen accept AND the resource
    # to be echoed back in the PAYMENT-SIGNATURE payload. The V1 shape
    # was {x402Version, scheme, network, payload: {signature, authorization}}
    # but V2 (which CMC uses, x402Version: 2 in the 402 body) is:
    #   {x402Version, accepted: {scheme, network, ...},
    #    resource:  {url, description, mimeType},
    #    payload:   {signature, authorization}}
    # Without the `accepted` and `resource` keys the server returns:
    #   "Missing accepted in PAYMENT-SIGNATURE payload (x402 V2)"
    #   "payment header resource is null"
    # The resource comes from `req.extra` if the chosen accept preserved
    # it; otherwise we leave it out and let the server error out so
    # we can see the diagnostic in the log.
    resource = req.resource
    payload = {
        "x402Version": 2,
        "accepted": {
            "scheme":   req.scheme,
            "network":  req.network,
            "asset":    req.token,
            "payTo":    req.payTo,
            "amount":   str(req.amount),
            # v2.2.2: include `maxTimeoutSeconds` and `extra` so the
            # server's permit2/eip3009 witness validation has the full
            # accept description (otherwise it returns 'permit2
            # authorization or witness is null' even when we picked
            # the eip3009 accept).
            "maxTimeoutSeconds": (req.extra or {}).get("maxTimeoutSeconds", 60) if isinstance(req.extra, dict) else 60,
        },
        "payload": {
            "signature": signature_hex,
            "authorization": {
                **message,
                "from":  message["from"],
                "to":    message["to"],
                "value": str(message["value"]),
            },
        },
    }
    if isinstance(req.extra, dict) and req.extra:
        # Preserve any `extra` fields the server expects (e.g.,
        # assetTransferMethod, name, version, x402PaymentConfigId).
        payload["accepted"]["extra"] = req.extra
    if resource is not None:
        payload["resource"] = resource
    return base64.b64encode(json.dumps(payload).encode()).decode()


def build_x402_payment_sync(
    wallet,
    req: PaymentRequirements,
    chain_id: int = DEFAULT_CHAIN_ID,
    token_address: str = DEFAULT_TOKEN_ADDRESS,
) -> str:
    """Synchronous version for tests + replay harness."""
    domain = {
        **USDC_EIP712_DOMAIN,
        "chainId": chain_id,
        "verifyingContract": Web3.to_checksum_address(token_address),
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


# --- balance polling for the wizard ---

def _get_web3(rpc_url: str):
    """Return a Web3 instance for the given RPC URL (mockable seam for tests)."""
    return _W3(_W3.HTTPProvider(rpc_url, request_kwargs={"timeout": 5.0}))


def check_balance(
    rpc_urls: list[str],
    holder: str,
    token: str,
) -> Decimal:
    """Read the USDC balance of `holder` from one of the given Base RPCs.

    Rotates through `rpc_urls` on connection failure (same pattern as BSCClient).
    Returns a Decimal in the token's smallest unit (USDC has 6 decimals, so
    divide by 1_000_000 to get human-readable USDC).
    """
    ERC20_BALANCE_OF = "0x70a08231"  # keccak("balanceOf(address)")[:4]
    # Concatenate the 4-byte selector with the 32-byte padded address
    # (the selector is 0x-prefixed; the address arg is NOT, to avoid
    # producing "0x70a082310xab..." which is not valid hex).
    padded = holder[2:].lower().rjust(64, "0")
    data = ERC20_BALANCE_OF + padded

    last_err: Exception | None = None
    for url in rpc_urls:
        try:
            w3 = _get_web3(url)
            raw = w3.eth.call({"to": _W3.to_checksum_address(token), "data": data})
            return Decimal(int.from_bytes(raw, "big"))
        except Exception as e:  # noqa: BLE001
            log.warning("check_balance: %s failed: %s", url, e)
            last_err = e
    raise RuntimeError(f"check_balance: all {len(rpc_urls)} RPCs failed: {last_err}")
