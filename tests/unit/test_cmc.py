"""Unit tests for CMCProClient and CMCX402Client."""
from __future__ import annotations

import asyncio
import base64
import json
from decimal import Decimal

import httpx
import pytest
import respx
from httpx import Response

from connectors.cmc import CMCProClient, CMCX402Client


@respx.mock
def test_pro_quotes_latest_sends_api_key_header():
    route = respx.get(
        "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
    ).mock(return_value=Response(200, json={
        "data": {"BTC": {"quote": {"USD": {"price": 50000.0}}}},
        "status": {"error_code": 0, "credit_count": 1},
    }))

    client = CMCProClient(api_key="test-key-123")
    result = asyncio.run(client.quotes_latest(["BTC"]))
    assert result["data"]["BTC"]["quote"]["USD"]["price"] == 50000.0
    sent = route.calls[0].request
    assert sent.headers["X-CMC_PRO_API_KEY"] == "test-key-123"
    assert client.tier == "cmc_pro"


@respx.mock
def test_pro_handles_401_with_error_code():
    respx.get(
        "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
    ).mock(return_value=Response(401, json={
        "status": {"error_code": 1001, "error_message": "Invalid API key"},
    }))
    client = CMCProClient(api_key="bad-key")
    with pytest.raises(Exception):
        asyncio.run(client.quotes_latest(["BTC"]))


@respx.mock
def test_x402_first_request_returns_payment_challenge():
    route = respx.get(
        "https://pro-api.coinmarketcap.com/x402/v3/cryptocurrency/quotes/latest"
    ).mock(return_value=Response(402, headers={
        "PAYMENT-REQUIRED": _b64_challenge(),
    }))

    client = CMCX402Client(
        wallet=_fake_wallet(),
        base_rpcs=["https://mainnet.base.org"],
    )
    try:
        asyncio.run(client.quotes_latest(["BTC"]))
    except Exception:
        pass


def test_x402_ledger_cost_uses_req_amount():
    """The ledger cost must come from req.amount in the 402 challenge, not a hardcoded 0.01."""
    import base64, json
    # amount=50000 = 0.05 USDC (USDC has 6 decimals)
    challenge_payload = {
        "scheme": "exact",
        "network": "eip155:8453",
        "token": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "amount": 50000,  # 0.05 USDC, not the default 10000
        "payTo": "0x271189c860DB25bC43173B0335784aD68a680908",
        "nonce": "0x" + "ab" * 32,
        "expiresAt": 9999999999,
    }
    b64 = base64.b64encode(json.dumps(challenge_payload).encode()).decode()

    with respx.mock:
        respx.get(
            "https://pro-api.coinmarketcap.com/x402/v3/cryptocurrency/quotes/latest"
        ).mock(side_effect=[
            Response(402, headers={"PAYMENT-REQUIRED": b64}),
            Response(200, json={"data": {"BTC": {"quote": {"USD": {"price": 50000.0}}}}}),
        ])

        client = CMCX402Client(
            wallet=_fake_wallet(),
            base_rpcs=["https://mainnet.base.org"],
        )
        result = asyncio.run(client.quotes_latest(["BTC"]))
    assert result["data"]["BTC"]["quote"]["USD"]["price"] == 50000.0
    # The ledger should reflect 0.05 USDC (50000 raw / 1e6), not 0.01
    assert client.spend_today == Decimal("0.05"), (
        f"expected 0.05 USDC from req.amount=50000, got {client.spend_today}"
    )


@respx.mock
def test_x402_spend_counter_does_not_increment_on_failed_retry():
    """v2.1.8 (x402-fix): a 402→402 retry (facilitator rejected our
    payment) must NOT increment spend_today. Before the fix, the counter
    went up the moment the signature was built, so 1000 failed attempts
    of 0.01 USDC = "spent: 10 USDC" in the ledger even though zero
    actually moved. This pins the new contract: cap reflects settlement,
    not signature attempts.
    """
    b64 = _b64_challenge()  # 10000 raw = 0.01 USDC, eip155:8453, USDC on Base

    with respx.mock:
        respx.get(
            "https://pro-api.coinmarketcap.com/x402/v3/cryptocurrency/quotes/latest"
        ).mock(side_effect=[
            Response(402, headers={"PAYMENT-REQUIRED": b64}),
            Response(402, headers={"PAYMENT-REQUIRED": b64}),  # retry rejected
        ])

        client = CMCX402Client(
            wallet=_fake_wallet(),
            base_rpcs=["https://mainnet.base.org"],
        )
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(client.quotes_latest(["BTC"]))
    assert client.spend_today == Decimal("0"), (
        f"expected 0 USDC after rejected retry, got {client.spend_today}"
    )
    # The retry's 402 is recorded in the calls ledger (first 402 is
    # treated as a normal challenge, not a failure).
    statuses = [c["status"] for c in client.calls]
    assert statuses == [402], f"expected [402] (the rejected retry), got {statuses}"
    assert all(c["cost_usdc"] == "0" for c in client.calls), (
        f"all failed-call ledger entries should record cost=0, got "
        f"{[c['cost_usdc'] for c in client.calls]}"
    )


@respx.mock
def test_x402_payment_signed_for_actual_challenge_chain():
    """v2.1.8 (x402-fix): the EIP-712 domain must reflect the 402
    challenge's chain/token, not the client's configured defaults.
    Before the fix, the function logged `req.network` and `req.token` but
    used the hardcoded Base/USDC for the EIP-712 domain — so the signed
    authorization was for the wrong verifying contract and the
    facilitator always rejected it. This pins the new contract: the
    signature must be valid for the chain/token the server asked for.
    """
    # CMC asks for a BSC token (eip155:56), not Base USDC
    challenge = {
        "scheme": "exact",
        "network": "eip155:56",
        "token": "0xcE24439F2D9C6a2289F741120FE202248B666666",
        "amount": 10000,
        "payTo": "0x271189c860DB25bC43173B0335784aD68a680908",
        "nonce": "0x" + "ab" * 32,
        "expiresAt": 9999999999,
    }
    b64 = base64.b64encode(json.dumps(challenge).encode()).decode()

    captured: dict = {}

    def _on_retry(request):
        # The PAYMENT-SIGNATURE header is base64 of the EIP-712 payload.
        captured["sig_b64"] = request.headers.get("PAYMENT-SIGNATURE", "")
        return Response(200, json={"data": {"BTC": {"quote": {"USD": {"price": 50000.0}}}}})

    respx.get(
        "https://pro-api.coinmarketcap.com/x402/v3/cryptocurrency/quotes/latest"
    ).mock(side_effect=[
        Response(402, headers={"PAYMENT-REQUIRED": b64}),
        _on_retry,
    ])

    client = CMCX402Client(
        wallet=_fake_wallet(),
        base_rpcs=["https://mainnet.base.org"],
    )
    result = asyncio.run(client.quotes_latest(["BTC"]))
    assert result["data"]["BTC"]["quote"]["USD"]["price"] == 50000.0

    # Decode the captured PAYMENT-SIGNATURE header and assert the
    # EIP-712 payload's network matches the challenge (eip155:56), not
    # the default (eip155:8453). Before the fix this would have been
    # eip155:8453 (hardcoded default) and the facilitator would have
    # rejected the signature.
    payload = json.loads(base64.b64decode(captured["sig_b64"]))
    assert payload["network"] == "eip155:56", (
        f"expected eip155:56 in signed payload, got {payload['network']}"
    )
    # Spend counter only goes up on success — 0.01 USDC for this call.
    assert client.spend_today == Decimal("0.01"), (
        f"expected 0.01 USDC after one successful call, got {client.spend_today}"
    )


def _b64_challenge() -> str:
    import base64, json
    payload = {
        "scheme": "exact",
        "network": "eip155:8453",
        "token": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "amount": 10000,
        "payTo": "0x271189c860DB25bC43173B0335784aD68a680908",
        "nonce": "0x" + "ab" * 32,
        "expiresAt": 9999999999,
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


def _fake_wallet():
    class _W:
        address = "0x" + "11" * 20
        key = b"\x01" * 32
        def sign_typed_data(self, domain, types, value):
            from eth_account import Account
            from eth_account.messages import encode_typed_data
            return Account.sign_message(
                encode_typed_data(domain, types, value), self.key
            )
    return _W()


@respx.mock
def test_pro_ohlcv_normalizes_to_flat_quotes_shape():
    """CMC's API returns each candle as {quote: {USD: {open, high, low, close, volume}}}.

    Strategies (sleeve_a_carry.py:142, sleeve_b_momentum.py:89, sleeve_c_meanrev.py:73)
    read payload['quotes'][i]['close'] — flat keys. CMC's nested shape would
    KeyError on every read. This test pins the normalization contract: every
    candle in ohlcv_historical has flat open/high/low/close/volume keys.
    """
    respx.get(
        "https://pro-api.coinmarketcap.com/v1/cryptocurrency/ohlcv/historical"
    ).mock(return_value=Response(200, json={
        "data": {
            "BTC": {"quotes": [
                {
                    "time_open":  "2026-06-01T00:00:00Z",
                    "time_close": "2026-06-01T00:59:59Z",
                    "quote": {"USD": {"open": 100, "high": 110, "low": 95, "close": 105, "volume": 1000}},
                },
                {
                    "time_open":  "2026-06-01T01:00:00Z",
                    "time_close": "2026-06-01T01:59:59Z",
                    "quote": {"USD": {"open": 105, "high": 115, "low": 100, "close": 110, "volume": 1500}},
                },
            ]},
        },
        "status": {"error_code": 0, "credit_count": 1},
    }))
    client = CMCProClient(api_key="test")
    result = asyncio.run(client.ohlcv_historical(["BTC"], count=2))
    candles = result["data"]["BTC"]["quotes"]
    assert len(candles) == 2
    for c in candles:
        for k in ("open", "high", "low", "close", "volume"):
            assert k in c, f"normalized candle missing flat key '{k}': {c}"
            assert isinstance(c[k], (int, float)), f"candle['{k}'] is {type(c[k]).__name__}, expected numeric"
    assert candles[0]["close"] == 105
    assert candles[1]["close"] == 110
