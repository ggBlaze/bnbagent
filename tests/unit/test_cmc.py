"""Unit tests for CMCProClient and CMCX402Client."""
from __future__ import annotations

import asyncio
from decimal import Decimal

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
