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
        """Pick the source from config["data_source"]["tier"]."""
        from .cmc import CMCProClient, CMCX402Client
        from .binance import BinanceClient

        ds = config.get("data_source", {})
        tier = ds.get("tier", "mock")
        if tier == "cmc_pro" and ds.get("cmc_api_key"):
            return cls(CMCProClient(api_key=ds["cmc_api_key"]))
        if tier == "x402" and wallet is not None:
            return cls(CMCX402Client(
                wallet=wallet,
                base_rpcs=ds.get("base_rpcs", _DEFAULT_BASE_RPCS),
            ))
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
        candles = [{"close": 1.0, "high": 1.0, "low": 1.0, "open": 1.0} for _ in range(count)]
        return {"data": {s: candles for s in symbols}, "status": {"error_code": 0, "note": "mock"}}

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
