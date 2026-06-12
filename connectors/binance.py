"""Binance public API data source.

Implements the MarketDataSource Protocol using https://api.binance.com/api/v3/.
Free, no auth. Covers prices (quotes_latest) and OHLCV (ohlcv_historical).
CMC-only methods (fear_and_greed, global_metrics, dex_listings, exchange_listings)
raise NotImplementedError.
"""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)


class BinanceClient:
    BASE = "https://api.binance.com/api/v3"

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))

    @property
    def tier(self) -> str:
        return "binance"

    @property
    def status(self) -> dict:
        return {"tier": "binance", "base": self.BASE}

    async def close(self):
        await self._client.aclose()

    async def quotes_latest(self, symbols: list[str], convert: str = "USD") -> dict:
        binance_symbols = [f"{s.upper()}USDT" for s in symbols]
        params = {"symbols": str(binance_symbols).replace("'", '"')}
        resp = await self._client.get(f"{self.BASE}/ticker/price", params=params)
        resp.raise_for_status()
        rows = resp.json()
        out = {}
        for row in rows:
            sym = row["symbol"].replace("USDT", "")
            out[sym] = {"quote": {convert: {"price": row["price"]}}}
        return {"data": out, "status": {"error_code": 0, "note": "binance"}}

    async def ohlcv_historical(
        self, symbols: list[str], time_period: str = "hour",
        count: int = 24, convert: str = "USD",
    ) -> dict:
        interval = {"hour": "1h", "day": "1d", "minute": "1m"}.get(time_period, "1h")
        out = {}
        for sym in symbols:
            params = {"symbol": f"{sym.upper()}USDT", "interval": interval, "limit": count}
            resp = await self._client.get(f"{self.BASE}/klines", params=params)
            resp.raise_for_status()
            rows = resp.json()
            # Wrap each symbol's candles in a 'quotes' key so strategies
            # (sleeve_a_carry.py:142, sleeve_b_momentum.py:89, sleeve_c_meanrev.py:73)
            # can read payload['quotes'] uniformly across all data sources.
            # CMCProClient + MockClient already use this shape; Binance was the
            # odd one out and the inconsistency caused every tick to crash
            # with AttributeError in the live PnL window.
            out[sym] = {
                "quotes": [
                    {
                        "open": r[1], "high": r[2], "low": r[3], "close": r[4],
                        "volume": r[5], "open_time": r[0], "close_time": r[6],
                    }
                    for r in rows
                ]
            }
        return {"data": out, "status": {"error_code": 0, "note": "binance"}}

    # --- unsupported methods ---

    async def cmc_rank_map(self) -> dict[str, int]:
        raise NotImplementedError("BinanceClient: cmc_rank_map has no Binance equivalent")

    async def global_metrics(self) -> dict:
        raise NotImplementedError("BinanceClient: global_metrics has no Binance equivalent")

    async def fear_and_greed(self) -> dict:
        raise NotImplementedError("BinanceClient: fear_and_greed has no Binance equivalent")

    async def dex_listings(self, limit: int = 100) -> dict:
        raise NotImplementedError("BinanceClient: dex_listings has no Binance equivalent")

    async def exchange_listings(self, limit: int = 100) -> dict:
        raise NotImplementedError("BinanceClient: exchange_listings has no Binance equivalent")
