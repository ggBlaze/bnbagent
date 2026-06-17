"""F4: decode_payment_requirements must handle the canonical x402 envelope.

Production log:

  INFO connectors.x402: x402 pay: scheme=exact network=bsc token=
       amount=0 payTo= nonce=
  WARN strategies.sleeve_a_carry: cmc quote failed for ETH:
       zero amount in payment requirements

`network=bsc` is the parser default, `amount=0` is the parser default,
and all the other fields default to empty. That signature says the
decoder parsed SOMETHING but found none of the expected top-level keys.
The canonical x402 challenge body (per
https://github.com/coinbase/x402/blob/main/typescript/packages/legacy/x402/src/types/verify/x402Specs.ts)
is:

    {
      "x402Version": 1,
      "accepts": [
        {
          "scheme": "exact",
          "network": "...",
          "maxAmountRequired": "10000",  # STRING, not int
          "asset": "0x...",              # not "token"
          "payTo": "0x...",
          "maxTimeoutSeconds": 60,       # not "expiresAt"
          "resource": "...",
          "description": "...",
          "mimeType": "...",
          "extra": {...}
        }
      ]
    }

The CMC client base64-encodes this body and puts it in the
PAYMENT-REQUIRED header. The bnbagent decoder reads the top level
(`scheme`, `amount`, `payTo`) and finds nothing — every value falls to
its default and the sleeve aborts with "zero amount".

These tests pin the spec-compliant fixture so the decoder follows the
`accepts[0]` shape, reads `maxAmountRequired` as string-or-int, accepts
`asset` as alias for `token`, and synthesizes `expiresAt` from
`maxTimeoutSeconds` when given.
"""
from __future__ import annotations

import base64
import json
import time
from decimal import Decimal

import pytest

from connectors.x402 import (
    decode_payment_requirements,
    X402Required,
)


def _b64(payload: dict) -> str:
    return base64.b64encode(json.dumps(payload).encode()).decode()


# Canonical spec-shaped envelope CMC actually sends (per x402Specs.ts).
_REAL_CMC_CHALLENGE = {
    "x402Version": 1,
    "accepts": [
        {
            "scheme": "exact",
            "network": "eip155:8453",
            "maxAmountRequired": "10000",   # 0.01 USDC, STRING per spec
            "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "payTo": "0x271189c860DB25bC43173B0335784aD68a680908",
            "maxTimeoutSeconds": 60,
            "resource": "https://pro-api.coinmarketcap.com/x402/v3/cryptocurrency/quotes/latest",
            "description": "CMC x402 quote",
            "mimeType": "application/json",
            "extra": {"name": "USD Coin", "version": "2"},
        }
    ],
}


def test_decoder_reads_canonical_accepts_envelope():
    """The canonical 402 body wraps the requirement in `accepts: [...]`.

    Today the decoder reads top-level keys, gets all defaults, and the
    sleeve aborts with "zero amount in payment requirements".
    """
    req = decode_payment_requirements(_b64(_REAL_CMC_CHALLENGE))
    assert req.scheme == "exact"
    assert req.network == "eip155:8453"
    assert req.amount == 10000, (
        f"maxAmountRequired (string '10000') must parse to int 10000; "
        f"got {req.amount}"
    )
    assert req.token == "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    assert req.payTo == "0x271189c860DB25bC43173B0335784aD68a680908"


def test_decoder_max_amount_required_can_be_string_or_int():
    """The spec defines maxAmountRequired as `z.string().refine(isInteger)`
    so it arrives as a quoted integer. We also tolerate raw int for
    older / non-conforming servers."""
    string_form = _b64({"accepts": [{"scheme": "exact", "network": "eip155:8453",
                                       "maxAmountRequired": "50000",
                                       "asset": "0x" + "33" * 20,
                                       "payTo": "0x" + "44" * 20,
                                       "maxTimeoutSeconds": 60}]})
    int_form = _b64({"accepts": [{"scheme": "exact", "network": "eip155:8453",
                                    "maxAmountRequired": 50000,
                                    "asset": "0x" + "33" * 20,
                                    "payTo": "0x" + "44" * 20,
                                    "maxTimeoutSeconds": 60}]})
    assert decode_payment_requirements(string_form).amount == 50000
    assert decode_payment_requirements(int_form).amount == 50000


def test_decoder_reads_asset_as_alias_for_token():
    """The canonical field name is `asset`; the older code expected `token`."""
    payload = _b64({"accepts": [{"scheme": "exact", "network": "eip155:8453",
                                  "maxAmountRequired": "10000",
                                  "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                                  "payTo": "0x" + "ab" * 20,
                                  "maxTimeoutSeconds": 60}]})
    req = decode_payment_requirements(payload)
    assert req.token == "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


def test_decoder_synthesizes_expires_at_from_max_timeout_seconds():
    """The spec gives a relative `maxTimeoutSeconds`; the wallet signing
    needs an absolute `validBefore`. Decoder should compute
    `now + maxTimeoutSeconds` so build_x402_payment_sync has something
    sensible to sign.

    Uses 120 (not 60) to distinguish from the legacy default-fallback
    of `time.time() + 60` — a green test must read the field, not just
    happen to match the old default."""
    before = int(time.time())
    payload = _b64({"accepts": [{"scheme": "exact", "network": "eip155:8453",
                                  "maxAmountRequired": "10000",
                                  "asset": "0x" + "33" * 20,
                                  "payTo": "0x" + "44" * 20,
                                  "maxTimeoutSeconds": 120}]})
    req = decode_payment_requirements(payload)
    after = int(time.time())
    assert before + 120 - 1 <= req.expiresAt <= after + 120 + 1, (
        f"expiresAt should be ~now+120s (read from maxTimeoutSeconds=120); "
        f"got {req.expiresAt}, window=[{before+120-1}, {after+120+1}]"
    )


def test_decoder_picks_first_accept_when_multiple():
    """A server can offer multiple payment options (e.g. USDC on Base AND
    on BSC). For now we pick the first one — same behaviour as the TS
    SDK's selectPaymentRequirements default."""
    payload = _b64({"accepts": [
        {"scheme": "exact", "network": "eip155:8453",
         "maxAmountRequired": "10000",
         "asset": "0x" + "11" * 20, "payTo": "0x" + "22" * 20,
         "maxTimeoutSeconds": 60},
        {"scheme": "exact", "network": "eip155:56",
         "maxAmountRequired": "20000",
         "asset": "0x" + "33" * 20, "payTo": "0x" + "44" * 20,
         "maxTimeoutSeconds": 60},
    ]})
    req = decode_payment_requirements(payload)
    assert req.amount == 10000
    assert req.network == "eip155:8453"


def test_decoder_legacy_top_level_shape_still_works():
    """The pre-fix top-level shape (used by our own test fixtures and
    older CMC versions) must keep parsing."""
    payload = _b64({"scheme": "exact", "network": "eip155:8453",
                    "amount": 10000, "token": "0x" + "33" * 20,
                    "payTo": "0x" + "44" * 20, "nonce": "0x" + "ab" * 32,
                    "expiresAt": 9999999999})
    req = decode_payment_requirements(payload)
    assert req.amount == 10000
    assert req.expiresAt == 9999999999
    assert req.nonce == "0x" + "ab" * 32


def test_decoder_empty_accepts_array_raises():
    """`accepts: []` means the server didn't offer any payment option —
    can't proceed."""
    payload = _b64({"x402Version": 1, "accepts": []})
    with pytest.raises(X402Required):
        decode_payment_requirements(payload)


def test_decoder_top_level_missing_everything_still_raises_on_zero():
    """If the decoder somehow gets an empty dict, the downstream
    x402_pay still rejects via the existing `amount <= 0` guard. This
    test pins the decoder's tolerance: it must NOT silently substitute
    a non-zero amount."""
    payload = _b64({})
    req = decode_payment_requirements(payload)
    assert req.amount == 0  # the existing guard catches this downstream


def test_decoder_preserves_extra_block_from_accepts_item():
    """The `extra` field on the accept item (USDC name/version, fee
    payer hints, etc.) flows into PaymentRequirements.extra so callers
    that need it (EIP-712 domain name override, etc.) can find it."""
    payload = _b64({"accepts": [{"scheme": "exact", "network": "eip155:8453",
                                  "maxAmountRequired": "10000",
                                  "asset": "0x" + "33" * 20,
                                  "payTo": "0x" + "44" * 20,
                                  "maxTimeoutSeconds": 60,
                                  "extra": {"name": "USD Coin", "version": "2",
                                            "feePayer": "facilitator"}}]})
    req = decode_payment_requirements(payload)
    assert req.extra.get("name") == "USD Coin"
    assert req.extra.get("feePayer") == "facilitator"
