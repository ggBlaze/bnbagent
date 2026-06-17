"""F2: Binance OHLCV numeric fields must be float, not str.

Binance `/api/v3/klines` returns each candle as a 12-tuple where the OHLCV
values are JSON strings (e.g. `"105"`). Sleeve C reads `quotes[i]["close"]`
and subtracts directly:

    ret_1h = (quotes[-1]["close"] - quotes[-2]["close"]) / quotes[-2]["close"]

If close stays a string, this raises:

    TypeError: unsupported operand type(s) for -: 'str' and 'str'

(observed at strategies/sleeve_c_meanrev.py:80 in the live PnL window).

The fix is to cast numeric fields to float at the connector boundary so every
downstream consumer gets the same shape regardless of upstream source
(MockClient + CMCProClient already return floats; Binance was the odd one).
"""
from __future__ import annotations

import asyncio

import respx
from httpx import Response

from connectors.binance import BinanceClient


@respx.mock
def test_ohlcv_close_is_float_not_str():
    """The bug: `close` came through as `str` and subtraction crashed.

    Pins the contract that the connector casts to float so strategies can
    do arithmetic without coercion at every call site.
    """
    respx.get("https://api.binance.com/api/v3/klines").mock(
        return_value=Response(200, json=[
            [1700000000000, "100", "110", "95", "105", "1000",
             1699999999999, "105000", 100, "500", "52500", "0"],
        ])
    )
    client = BinanceClient()
    result = asyncio.run(client.ohlcv_historical(["BTC"], count=1))
    candle = result["data"]["BTC"]["quotes"][0]
    assert isinstance(candle["close"], float), (
        f"close must be float (sleeve C does arithmetic on it); "
        f"got {type(candle['close']).__name__}={candle['close']!r}"
    )
    assert candle["close"] == 105.0


@respx.mock
def test_ohlcv_all_numeric_fields_are_float():
    """All OHLCV numeric fields are cast — not just close.

    `open`, `high`, `low`, `volume` are also used in strategies and
    risk calcs; cast them uniformly at the boundary.
    """
    respx.get("https://api.binance.com/api/v3/klines").mock(
        return_value=Response(200, json=[
            [1700000000000, "100.5", "110.25", "95.75", "105.125", "1000.5",
             1699999999999, "105000", 100, "500", "52500", "0"],
        ])
    )
    client = BinanceClient()
    result = asyncio.run(client.ohlcv_historical(["BTC"], count=1))
    candle = result["data"]["BTC"]["quotes"][0]
    for k in ("open", "high", "low", "close", "volume"):
        assert isinstance(candle[k], float), f"{k} must be float, got {type(candle[k]).__name__}"
    assert candle["open"] == 100.5
    assert candle["high"] == 110.25
    assert candle["low"] == 95.75
    assert candle["close"] == 105.125
    assert candle["volume"] == 1000.5


@respx.mock
def test_ohlcv_subtraction_works_on_returned_candles():
    """Direct arithmetic on returned candles must not raise.

    This mirrors strategies/sleeve_c_meanrev.py:80 exactly. If it raises
    `TypeError: unsupported operand type(s) for -: 'str' and 'str'`,
    the fix didn't land.
    """
    respx.get("https://api.binance.com/api/v3/klines").mock(
        return_value=Response(200, json=[
            [1700000000000, "100", "110", "95", "100", "1000",
             1699999999999, "100000", 100, "500", "50000", "0"],
            [1700003600000, "100", "115", "100", "110", "1500",
             1700003599999, "165000", 150, "750", "82500", "0"],
        ])
    )
    client = BinanceClient()
    result = asyncio.run(client.ohlcv_historical(["BTC"], count=2))
    quotes = result["data"]["BTC"]["quotes"]
    # This is exactly the expression that crashed in production.
    ret_1h = (quotes[-1]["close"] - quotes[-2]["close"]) / quotes[-2]["close"]
    assert ret_1h == 0.10  # (110 - 100) / 100


@respx.mock
def test_quotes_latest_price_is_float():
    """The live-quote path also gets a float for consistency.

    Sleeve C already wraps in Decimal(str(...)) so it survives a string,
    but other consumers (e.g. dashboard tiles, backtest replay) don't.
    Cast at the boundary.
    """
    respx.get("https://api.binance.com/api/v3/ticker/price").mock(
        return_value=Response(200, json=[
            {"symbol": "BTCUSDT", "price": "50000.5"},
        ])
    )
    client = BinanceClient()
    result = asyncio.run(client.quotes_latest(["BTC"]))
    price = result["data"]["BTC"]["quote"]["USD"]["price"]
    assert isinstance(price, float), f"price must be float, got {type(price).__name__}"
    assert price == 50000.5
