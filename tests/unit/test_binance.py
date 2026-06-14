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
    # Wrapped in 'quotes' to match the strategy-expected shape (CMCProClient +
    # MockClient). See fix-p0 below for context.
    candles = result["data"]["BTC"]["quotes"]
    assert len(candles) == 2
    assert candles[0]["close"] == "105"
    assert candles[1]["close"] == "110"


@respx.mock
def test_ohlcv_historical_matches_strategy_shape():
    """Lock the response shape so strategies can read payload['quotes'].

    Strategies (sleeve_a_carry.py:142, sleeve_b_momentum.py:89, sleeve_c_meanrev.py:73)
    read payload.get('quotes', []) on each entry in ohlc['data']. If Binance
    returned a bare list (the pre-fix shape), payload.get raised AttributeError
    ('list' has no attribute 'get') and the live PnL window produced zero
    signals on every tick. This test pins the contract.
    """
    respx.get("https://api.binance.com/api/v3/klines").mock(
        return_value=Response(200, json=[
            [1700000000000, "100", "110", "95", "105", "1000", 1699999999999, "105000", 100, "500", "52500", "0"],
        ])
    )
    import asyncio
    client = BinanceClient()
    result = asyncio.run(client.ohlcv_historical(["BTC"], count=1))
    payload = result["data"]["BTC"]
    assert isinstance(payload, dict), f"payload must be a dict (with 'quotes' key), got {type(payload).__name__}"
    assert "quotes" in payload
    assert isinstance(payload["quotes"], list)
    assert len(payload["quotes"]) == 1
    candle = payload["quotes"][0]
    # Strategies read these flat keys. If any are nested under quote.USD, the
    # strategy's quotes[i]["close"] raises KeyError.
    for k in ("open", "high", "low", "close", "volume"):
        assert k in candle, f"candle missing flat key '{k}'"


def test_unsupported_method_raises():
    import asyncio
    client = BinanceClient()
    with pytest.raises(NotImplementedError):
        asyncio.run(client.fear_and_greed())


def test_status_includes_tier():
    client = BinanceClient()
    assert client.status["tier"] == "binance"


# --- v2.1.7: per-symbol resilience in BinanceClient -----------------------

@respx.mock
def test_ohlcv_historical_silently_drops_unknown_symbols():
    """If one symbol in the batch isn't on Binance, the whole batch
    used to raise and crash the sleeves. Now we silently drop the
    unknown ones and return whatever worked.
    """
    respx.get("https://api.binance.com/api/v3/klines").mock(
        side_effect=[
            # First call: BTCUSDT works
            Response(200, json=[
                [1700000000000, "100", "110", "95", "105", "1000", 1699999999999, "105000", 100, "500", "52500", "0"],
            ]),
            # Second call: FOOUSDT \u2014 unknown symbol, Binance returns 400
            Response(400, json={"code": -1121, "msg": "Invalid symbol."}),
            # Third call: ETHUSDT works
            Response(200, json=[
                [1700000000000, "3000", "3100", "2900", "3050", "500", 1699999999999, "1525000", 50, "250", "762500", "0"],
            ]),
        ]
    )
    import asyncio
    client = BinanceClient()
    result = asyncio.run(client.ohlcv_historical(["BTC", "FOO", "ETH"], count=1))
    # BTC and ETH are returned; FOO is silently dropped
    assert "BTC" in result["data"]
    assert "FOO" not in result["data"]
    assert "ETH" in result["data"]


@respx.mock
def test_quotes_latest_falls_back_to_per_symbol_on_bulk_400():
    """Binance bulk /ticker/price returns 400 if any symbol in the
    batch is unknown. We catch and fall back to per-symbol requests,
    returning only the ones that work.
    """
    respx.get("https://api.binance.com/api/v3/ticker/price").mock(
        side_effect=[
            # First call: bulk with all 3 \u2014 fails because FOO unknown
            Response(400, json={"code": -1121, "msg": "Invalid symbol."}),
            # Then per-symbol: BTC works
            Response(200, json={"symbol": "BTCUSDT", "price": "50000.00"}),
            # FOO fails
            Response(400, json={"code": -1121, "msg": "Invalid symbol."}),
            # ETH works
            Response(200, json={"symbol": "ETHUSDT", "price": "3000.00"}),
        ]
    )
    import asyncio
    client = BinanceClient()
    result = asyncio.run(client.quotes_latest(["BTC", "FOO", "ETH"]))
    assert "BTC" in result["data"]
    assert result["data"]["BTC"]["quote"]["USD"]["price"] == "50000.00"
    assert "FOO" not in result["data"]
    assert "ETH" in result["data"]
    assert result["data"]["ETH"]["quote"]["USD"]["price"] == "3000.00"
