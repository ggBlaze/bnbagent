"""Test the MarketDataSource Protocol + 4 concrete clients + DataSourceRouter."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
import respx
from httpx import Response

from connectors.cmc import CMCProClient, CMCX402Client
from connectors.data_source import (
    DataSourceRouter,
    MarketDataSource,
    MockClient,
)
from connectors.binance import BinanceClient


# --- Protocol conformance ---

class _AllMethods:
    """Spy that implements the Protocol by recording which methods get called."""
    def __init__(self):
        self.calls = []

    async def quotes_latest(self, symbols, convert="USD"):
        self.calls.append(("quotes_latest", tuple(symbols), convert))
        return {"data": {s: {"quote": {"USD": {"price": 1.0}}} for s in symbols}}

    async def ohlcv_historical(self, symbols, time_period="hour", count=24, convert="USD"):
        self.calls.append(("ohlcv_historical", tuple(symbols), time_period, count, convert))
        return {"data": {s: [{"close": 1.0}] for s in symbols}}

    async def cmc_rank_map(self):
        self.calls.append(("cmc_rank_map",))
        return {"BTC": 1, "ETH": 2}

    async def global_metrics(self):
        self.calls.append(("global_metrics",))
        return {"data": {"quote": {"USD": {"total_market_cap": 1.0}}}}

    async def fear_and_greed(self):
        self.calls.append(("fear_and_greed",))
        return {"data": {"value": 50, "value_classification": "Neutral"}}

    async def dex_listings(self, limit=100):
        self.calls.append(("dex_listings", limit))
        return {"data": []}

    async def exchange_listings(self, limit=100):
        self.calls.append(("exchange_listings", limit))
        return {"data": []}

    @property
    def tier(self):
        return "spy"

    @property
    def status(self):
        return {"tier": "spy"}


def test_protocol_is_runtime_checkable():
    spy = _AllMethods()
    assert isinstance(spy, MarketDataSource)


def test_router_delegates_to_active_source():
    import asyncio

    async def go():
        spy = _AllMethods()
        router = DataSourceRouter(spy)
        await router.quotes_latest(["BTC"])
        await router.ohlcv_historical(["ETH"], count=5)
        await router.cmc_rank_map()
        await router.global_metrics()
        await router.fear_and_greed()
        await router.dex_listings()
        await router.exchange_listings()
        return spy.calls

    calls = asyncio.run(go())
    names = [c[0] for c in calls]
    assert names == [
        "quotes_latest", "ohlcv_historical", "cmc_rank_map",
        "global_metrics", "fear_and_greed", "dex_listings", "exchange_listings",
    ]


def test_router_hot_swap():
    import asyncio

    async def go():
        spy1, spy2 = _AllMethods(), _AllMethods()
        router = DataSourceRouter(spy1)
        await router.quotes_latest(["BTC"])
        router.set_source(spy2)
        await router.quotes_latest(["ETH"])
        return spy1.calls, spy2.calls

    s1, s2 = asyncio.run(go())
    assert [c[1] for c in s1] == [("BTC",)]
    assert [c[1] for c in s2] == [("ETH",)]


def test_router_tier_reflects_active_source():
    spy1, spy2 = _AllMethods(), _AllMethods()
    router = DataSourceRouter(spy1)
    assert router.tier == "spy"
    router.set_source(spy2)
    assert router.tier == "spy"
    assert router.status == {"tier": "spy"}


# --- Mock client ---

def test_mock_client_loads_fixture():
    client = MockClient()
    assert client.tier == "mock"
    import asyncio
    q = asyncio.run(client.quotes_latest(["BTC"]))
    assert "data" in q


def test_mock_client_fear_and_greed():
    client = MockClient()
    import asyncio
    fg = asyncio.run(client.fear_and_greed())
    assert fg["data"]["value_classification"] in ("Fear", "Neutral", "Greed")


def test_mock_ohlcv_matches_strategy_shape():
    """Mock ohlcv must wrap the candles in a 'quotes' key per symbol.

    Strategies (sleeve_a_carry.py:142) read payload['quotes'] — if the
    mock returns a bare list, the strategy silently falls back to its
    vol-fallback. This test asserts the wrapper shape.
    """
    import asyncio
    client = MockClient()
    result = asyncio.run(client.ohlcv_historical(["BTC", "ETH"], count=3))
    for sym in ("BTC", "ETH"):
        payload = result["data"][sym]
        assert isinstance(payload, dict), f"{sym} payload should be dict, got {type(payload)}"
        assert "quotes" in payload
        assert len(payload["quotes"]) == 3


# --- Tier identification ---

def test_each_client_reports_its_tier():
    mock = MockClient()
    assert mock.tier == "mock"
