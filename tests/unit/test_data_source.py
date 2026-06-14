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


# --- v2.1.7: HybridDataSource (x402 + Binance OHLCV) ----------------------

class _FakeX402:
    """Minimal stub matching the bits HybridDataSource uses."""
    def __init__(self):
        self.closed = False
        self.quotes_calls = []
        self.rank_calls = 0
    async def quotes_latest(self, symbols, convert="USD"):
        self.quotes_calls.append((tuple(symbols), convert))
        return {"data": {s: {"quote": {convert: {"price": 42.0}}} for s in symbols},
                "status": {"error_code": 0, "note": "fake-x402"}}
    async def cmc_rank_map(self):
        self.rank_calls += 1
        return {"BTC": 1, "ETH": 2}
    async def global_metrics(self):
        return {"data": {}, "status": {"error_code": 0}}
    async def fear_and_greed(self):
        return {"data": {"value": 50, "value_classification": "Neutral"},
                "status": {"error_code": 0}}
    async def dex_listings(self, limit=100):
        return {"data": [], "status": {"error_code": 0}}
    async def exchange_listings(self, limit=100):
        return {"data": [], "status": {"error_code": 0}}
    async def close(self):
        self.closed = True
    @property
    def status(self):
        return {"tier": "x402", "chain_id": 8453, "spend_today_usdc": "0"}


class _FakeBinance:
    """Minimal stub for the Binance half of the hybrid."""
    def __init__(self):
        self.closed = False
        self.ohlcv_calls = []
    async def ohlcv_historical(self, symbols, time_period="hour", count=24, convert="USD"):
        self.ohlcv_calls.append((tuple(symbols), time_period, count))
        return {"data": {s: {"quotes": [{"close": 100.0}]} for s in symbols},
                "status": {"error_code": 0, "note": "fake-binance"}}
    async def close(self):
        self.closed = True


def test_hybrid_routes_quotes_to_x402_and_ohlcv_to_binance():
    """The whole point of the hybrid: sponsor track for live prices,
    free OHLCV from Binance so the sleeves can actually compute signals."""
    import asyncio
    from connectors.data_source import HybridDataSource
    x402 = _FakeX402()
    binance = _FakeBinance()
    h = HybridDataSource(x402=x402, binance=binance)
    assert h.tier == "x402"  # user-facing tier is still x402
    async def go():
        q = await h.quotes_latest(["BTC", "ETH"])
        o = await h.ohlcv_historical(["BTC", "ETH"], time_period="hour", count=24)
        r = await h.cmc_rank_map()
        return q, o, r
    q, o, r = asyncio.run(go())
    # quotes go to x402
    assert x402.quotes_calls == [(("BTC", "ETH"), "USD")]
    assert q["data"]["BTC"]["quote"]["USD"]["price"] == 42.0
    # OHLCV goes to Binance
    assert binance.ohlcv_calls == [(("BTC", "ETH"), "hour", 24)]
    assert o["data"]["BTC"]["quotes"][0]["close"] == 100.0
    # cmc_rank_map goes to x402
    assert x402.rank_calls == 1
    assert r == {"BTC": 1, "ETH": 2}


def test_hybrid_status_reports_fallback_transparently():
    from connectors.data_source import HybridDataSource
    h = HybridDataSource(x402=_FakeX402(), binance=_FakeBinance())
    s = h.status
    # User-facing tier is x402
    assert s["tier"] == "x402"
    # The fallback is explicitly named so judges + operators can see it
    assert s["fallback"] == "binance"
    assert "ohlcv_historical" in s["fallback_for"]
    # The note explains the architecture
    assert "Hybrid" in s["note"]


def test_hybrid_closes_both_clients():
    import asyncio
    from connectors.data_source import HybridDataSource
    x402 = _FakeX402()
    binance = _FakeBinance()
    h = HybridDataSource(x402=x402, binance=binance)
    asyncio.run(h.close())
    assert x402.closed is True
    assert binance.closed is True


def test_hybrid_ohlcv_route_is_silent_on_unknown_symbols(monkeypatch):
    """When the underlying BinanceClient returns a partial OHLCV
    (some symbols missing because they aren't on Binance), the
    hybrid should still return whatever it got \u2014 the sleeves
    catch empty payloads and skip. We don't test BinanceClient
    directly here; this is a contract lock for the hybrid layer.
    """
    import asyncio
    from connectors.data_source import HybridDataSource

    class _PartialBinance:
        async def ohlcv_historical(self, symbols, time_period="hour", count=24, convert="USD"):
            # Only BTC has data; the rest (small-cap BEP-20s) are
            # silently dropped (this is the post-fix Binance behavior).
            return {
                "data": {"BTC": {"quotes": [{"close": 100.0}]}},
                "status": {"error_code": 0, "note": "partial"},
            }
    h = HybridDataSource(x402=_FakeX402(), binance=_PartialBinance())
    o = asyncio.run(h.ohlcv_historical(["BTC", "FOO", "BAR"]))
    assert "BTC" in o["data"]
    assert "FOO" not in o["data"]
    assert "BAR" not in o["data"]


def test_from_config_x402_constructs_hybrid_by_default(monkeypatch):
    """Picking 'x402' in the wizard now produces a HybridDataSource.

    The user-facing tier is still 'x402' (so the sponsor track credit
    + the x402 balance polling keep working), but OHLCV is served
    by Binance so the sleeves can actually trade.
    """
    from connectors.data_source import DataSourceRouter
    monkeypatch.delenv("BNBAGENT_X402_NO_BINANCE_FALLBACK", raising=False)
    class FakeWallet:
        address = "0x" + "a" * 40
    ds = DataSourceRouter.from_config(
        {"data_source": {"tier": "x402", "base_rpcs": ["https://x"]}},
        wallet=FakeWallet(),
    )
    assert ds.tier == "x402"
    assert type(ds.source).__name__ == "HybridDataSource"
    assert ds.source.status["fallback"] == "binance"


def test_from_config_x402_opt_out_uses_pure_x402(monkeypatch):
    """Setting BNBAGENT_X402_NO_BINANCE_FALLBACK=1 reverts to pure x402
    (which has no OHLCV \u2014 only useful if you know the agent will
    fall through to the daily trade floor).
    """
    from connectors.data_source import DataSourceRouter
    monkeypatch.setenv("BNBAGENT_X402_NO_BINANCE_FALLBACK", "true")
    class FakeWallet:
        address = "0x" + "a" * 40
    ds = DataSourceRouter.from_config(
        {"data_source": {"tier": "x402", "base_rpcs": ["https://x"]}},
        wallet=FakeWallet(),
    )
    assert type(ds.source).__name__ == "CMCX402Client"
    assert ds.source.status["tier"] == "x402"
    assert "fallback" not in ds.source.status
