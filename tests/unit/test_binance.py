"""Unit tests for BinanceClient."""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from connectors.binance import BinanceClient


@respx.mock
def test_quotes_latest_parses_ticker_price():
    respx.get("https://api.binance.com/api/v3/ticker/price").mock(
        return_value=Response(200, json=[
            {"symbol": "BTCUSDT", "price": "50000.00"},
            {"symbol": "ETHUSDT", "price": "3000.00"},
        ])
    )
    import asyncio
    client = BinanceClient()
    result = asyncio.run(client.quotes_latest(["BTC", "ETH"]))
    assert result["data"]["BTC"]["quote"]["USD"]["price"] == "50000.00"
    assert result["data"]["ETH"]["quote"]["USD"]["price"] == "3000.00"
    assert client.tier == "binance"


@respx.mock
def test_ohlcv_historical_parses_klines():
    respx.get("https://api.binance.com/api/v3/klines").mock(
        return_value=Response(200, json=[
            [1700000000000, "100", "110", "95", "105", "1000", 1699999999999, "105000", 100, "500", "52500", "0"],
            [1700003600000, "105", "115", "100", "110", "1500", 1700003599999, "165000", 150, "750", "82500", "0"],
        ])
    )
    import asyncio
    client = BinanceClient()
    result = asyncio.run(client.ohlcv_historical(["BTC"], count=2))
    candles = result["data"]["BTC"]
    assert len(candles) == 2
    assert candles[0]["close"] == "105"
    assert candles[1]["close"] == "110"


def test_unsupported_method_raises():
    import asyncio
    client = BinanceClient()
    with pytest.raises(NotImplementedError):
        asyncio.run(client.fear_and_greed())


def test_status_includes_tier():
    client = BinanceClient()
    assert client.status["tier"] == "binance"
