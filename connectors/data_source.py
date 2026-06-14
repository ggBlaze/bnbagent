"""MarketDataSource Protocol + DataSourceRouter + MockClient.

The router sits between the strategies and the data sources. The active
source is one of CMCProClient, CMCX402Client, BinanceClient, or MockClient.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)


@runtime_checkable
class MarketDataSource(Protocol):
    """The interface every data source must implement."""

    async def quotes_latest(self, symbols: list[str], convert: str = "USD") -> dict: ...
    async def ohlcv_historical(
        self, symbols: list[str], time_period: str = "hour",
        count: int = 24, convert: str = "USD",
    ) -> dict: ...
    async def cmc_rank_map(self) -> dict[str, int]: ...
    async def global_metrics(self) -> dict: ...
    async def fear_and_greed(self) -> dict: ...
    async def dex_listings(self, limit: int = 100) -> dict: ...
    async def exchange_listings(self, limit: int = 100) -> dict: ...

    @property
    def tier(self) -> str: ...
    @property
    def status(self) -> dict: ...


class DataSourceRouter:
    """Holds one active MarketDataSource; delegates all calls to it."""

    def __init__(self, source: MarketDataSource):
        self._source = source

    def set_source(self, source: MarketDataSource) -> None:
        log.info("data source: %s → %s", self._source.tier, source.tier)
        self._source = source

    @property
    def source(self) -> MarketDataSource:
        return self._source

    async def quotes_latest(self, symbols: list[str], convert: str = "USD") -> dict:
        return await self._source.quotes_latest(symbols, convert)

    async def ohlcv_historical(
        self, symbols: list[str], time_period: str = "hour",
        count: int = 24, convert: str = "USD",
    ) -> dict:
        return await self._source.ohlcv_historical(symbols, time_period, count, convert)

    async def cmc_rank_map(self) -> dict[str, int]:
        return await self._source.cmc_rank_map()

    async def global_metrics(self) -> dict:
        return await self._source.global_metrics()

    async def fear_and_greed(self) -> dict:
        return await self._source.fear_and_greed()

    async def dex_listings(self, limit: int = 100) -> dict:
        return await self._source.dex_listings(limit)

    async def exchange_listings(self, limit: int = 100) -> dict:
        return await self._source.exchange_listings(limit)

    @property
    def tier(self) -> str:
        return self._source.tier

    @property
    def status(self) -> dict:
        return self._source.status

    @classmethod
    def from_config(cls, config: dict, wallet=None) -> "DataSourceRouter":
        """Pick the source from config["data_source"]["tier"].

        v2.1.7: when the user picks "x402", we now construct a
        HybridDataSource that uses x402 for live quotes + listings
        (the sponsor track, paid in USDC on Base) and Binance for
        OHLCV (free, no key, full coverage). x402 has no OHLCV
        endpoint, so the prior behavior was "agent makes 0 trades in
        x402 mode" — the hybrid fixes that. Opt out via
        BNBAGENT_X402_NO_BINANCE_FALLBACK=1 in the env.
        """
        from .cmc import CMCProClient, CMCX402Client
        from .binance import BinanceClient
        import os

        ds = config.get("data_source", {})
        tier = ds.get("tier", "mock")
        if tier == "cmc_pro" and ds.get("cmc_api_key"):
            return cls(CMCProClient(api_key=ds["cmc_api_key"]))
        if tier == "x402" and wallet is not None:
            x402_client = CMCX402Client(
                wallet=wallet,
                base_rpcs=ds.get("base_rpcs", _DEFAULT_BASE_RPCS),
            )
            # Default: hybrid x402 + Binance OHLCV. The sponsor track
            # (CMC, USDC on Base) is still actively used for live
            # quotes + listings; Binance silently fills in OHLCV. Set
            # BNBAGENT_X402_NO_BINANCE_FALLBACK=1 to opt out.
            no_fallback = os.environ.get("BNBAGENT_X402_NO_BINANCE_FALLBACK", "").lower() in (
                "1", "true", "yes", "on",
            )
            if no_fallback:
                return cls(x402_client)
            return cls(HybridDataSource(x402=x402_client, binance=BinanceClient()))
        if tier == "binance":
            return cls(BinanceClient())
        return cls(MockClient())


_DEFAULT_BASE_RPCS = [
    "https://mainnet.base.org",
    "https://base.publicnode.com",
    "https://1rpc.io/base",
]


class MockClient:
    """Returns hardcoded data from data/cmc_mock.json. No network calls."""

    def __init__(self, fixture_path: str | Path = "data/cmc_mock.json"):
        self._path = Path(fixture_path)
        with self._path.open() as f:
            self._data = json.load(f)

    @property
    def tier(self) -> str:
        return "mock"

    @property
    def status(self) -> dict:
        return {"tier": "mock", "source": str(self._path)}

    async def quotes_latest(self, symbols: list[str], convert: str = "USD") -> dict:
        return {
            "data": {
                s: {"quote": {convert: {"price": 1.0, "last_updated": "2026-01-01T00:00:00Z"}}}
                for s in symbols
            },
            "status": {"error_code": 0, "credit_count": 0, "note": "mock data"},
        }

    async def ohlcv_historical(
        self, symbols: list[str], time_period: str = "hour",
        count: int = 24, convert: str = "USD",
    ) -> dict:
        # Wrap in the same shape CMCProClient returns, so strategies
        # that read `payload["quotes"]` (e.g. sleeve_a_carry.py) work in mock mode.
        candles = [{"close": 1.0, "high": 1.0, "low": 1.0, "open": 1.0} for _ in range(count)]
        return {
            "data": {s: {"quotes": candles} for s in symbols},
            "status": {"error_code": 0, "note": "mock"},
        }

    async def cmc_rank_map(self) -> dict[str, int]:
        return self._data.get("cmc_rank_map", {})

    async def global_metrics(self) -> dict:
        return self._data.get("global_metrics", {"data": {}, "status": {"error_code": 0}})

    async def fear_and_greed(self) -> dict:
        return self._data.get("fear_and_greed", {
            "data": {"value": 50, "value_classification": "Neutral"},
            "status": {"error_code": 0},
        })

    async def dex_listings(self, limit: int = 100) -> dict:
        return {"data": self._data.get("dex_listings", [])[:limit], "status": {"error_code": 0}}

    async def exchange_listings(self, limit: int = 100) -> dict:
        return {"data": self._data.get("exchange_listings", [])[:limit], "status": {"error_code": 0}}


class HybridDataSource:
    """x402 sponsor track + Binance OHLCV fallback (v2.1.7).

    Why this exists
    ---------------
    The x402 endpoint of CoinMarketCap supports live quotes and
    listings, but does NOT have an OHLCV endpoint. Every strategy
    (sleeve_a_carry, sleeve_b_momentum, sleeve_c_meanrev) needs
    historical candles to compute its signals: realized vol, momentum
    strength, mean-reversion z-scores. Without OHLCV, the sleeves
    catch NotImplementedError on every tick, return no signals, and
    only the daily trade floor fires. In a contest window that's
    1 trade/day \u2014 not enough to show meaningful PnL.

    The fix: route per-method to the source that has the data.

      * quotes_latest   \u2192 x402  (sponsor track, USDC on Base)
      * cmc_rank_map    \u2192 x402  (sponsor track, /listings/latest)
      * ohlcv_historical \u2192 Binance (free, no key, has full history)
      * the other endpoints (global_metrics, fear_and_greed,
        dex_listings, exchange_listings) aren't used by any strategy,
        but the protocol requires them. We try x402 first; if it
        raises NotImplementedError, we return a sensible empty
        stub so the dashboard renders cleanly.

    The user-facing tier is still "x402" (so the wizard, the dashboard
    status, the x402 balance polling, and the BNB HACK 2026 sponsor
    credit all keep working as before). The status dict reports the
    fallback transparently. Opt out via
    BNBAGENT_X402_NO_BINANCE_FALLBACK=1.
    """

    def __init__(self, x402, binance):
        self._x402 = x402
        self._binance = binance

    @property
    def tier(self) -> str:
        # The user-facing tier is still x402. Operators checking
        # the dashboard or the sponsor-track credit should see the
        # same string they selected in the wizard.
        return "x402"

    @property
    def status(self) -> dict:
        x402_status = self._x402.status if hasattr(self._x402, "status") else {}
        return {
            **x402_status,
            "tier": "x402",  # explicit \u2014 the user's selection
            "fallback": "binance",
            "fallback_for": ["ohlcv_historical"],
            "note": (
                "Hybrid: x402 serves live quotes + listings (sponsor track, "
                "USDC on Base); Binance serves OHLCV (free, no key, full coverage). "
                "x402 has no OHLCV endpoint \u2014 the sleeves need it to compute "
                "signals, so the hybrid is the only way to actually trade in "
                "x402 mode. Opt out with BNBAGENT_X402_NO_BINANCE_FALLBACK=1."
            ),
        }

    async def close(self):
        if hasattr(self._x402, "close"):
            await self._x402.close()
        if hasattr(self._binance, "close"):
            await self._binance.close()

    # --- per-method routing ---

    async def quotes_latest(self, symbols, convert="USD"):
        return await self._x402.quotes_latest(symbols, convert)

    async def ohlcv_historical(self, symbols, time_period="hour", count=24, convert="USD"):
        return await self._binance.ohlcv_historical(symbols, time_period, count, convert)

    async def cmc_rank_map(self):
        return await self._x402.cmc_rank_map()

    async def global_metrics(self):
        try:
            return await self._x402.global_metrics()
        except NotImplementedError:
            return {"data": {}, "status": {"error_code": 0, "note": "x402: not implemented"}}

    async def fear_and_greed(self):
        try:
            return await self._x402.fear_and_greed()
        except NotImplementedError:
            return {
                "data": {"value": 50, "value_classification": "Neutral"},
                "status": {"error_code": 0, "note": "x402: not implemented"},
            }

    async def dex_listings(self, limit=100):
        try:
            return await self._x402.dex_listings(limit)
        except NotImplementedError:
            return {"data": [], "status": {"error_code": 0, "note": "x402: not implemented"}}

    async def exchange_listings(self, limit=100):
        try:
            return await self._x402.exchange_listings(limit)
        except NotImplementedError:
            return {"data": [], "status": {"error_code": 0, "note": "x402: not implemented"}}
