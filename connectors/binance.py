"""Binance public API data source.

Implements the MarketDataSource Protocol using https://api.binance.com/api/v3/.
Free, no auth. Covers prices (quotes_latest) and OHLCV (ohlcv_historical).
CMC-only methods (fear_and_greed, global_metrics, dex_listings, exchange_listings)
raise NotImplementedError.
"""
from __future__ import annotations

import logging

import httpx
import json

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
        # v2.1.7: prefer the bulk /ticker/price?symbols=[...] call (1 RPC
        # for the whole batch). If ANY symbol in the batch is unknown
        # to Binance, the bulk call returns 400 for the whole request
        # — so fall back to per-symbol requests and silently drop the
        # unknown ones. This matters for the hybrid x402+Binance
        # mode where the basket can include BEP-20s that aren't listed
        # on Binance.
        #
        # v2.1.8 (F2): cast `price` to float at the boundary. Binance
        # returns numeric fields as JSON strings; downstream consumers
        # (sleeve C arithmetic, dashboard tiles, backtest replay)
        # expect numbers. Single coercion here keeps the rest of the
        # codebase free of `float(str(...))` boilerplate.
        out = {}
        try:
            binance_symbols = [f"{s.upper()}USDT" for s in symbols]
            # v2.2.3: Binance bulk endpoint requires NO whitespace inside the
            # JSON array. `str(list).replace("'", '"')` produces
            # '["A", "B"]' (space after comma) which Binance rejects with
            # -1100 "Illegal characters found in parameter 'symbols'".
            # Use json.dumps with explicit separators to get '["A","B"]'.
            params = {"symbols": json.dumps(binance_symbols, separators=(",", ":"))}
            resp = await self._client.get(f"{self.BASE}/ticker/price", params=params)
            resp.raise_for_status()
            rows = resp.json()
            for row in rows:
                sym = row["symbol"].replace("USDT", "")
                out[sym] = {"quote": {convert: {"price": float(row["price"])}}}
        except Exception as e:
            log.debug("binance: bulk /ticker/price failed (%s), falling back to per-symbol", e)
            for sym in symbols:
                try:
                    resp = await self._client.get(
                        f"{self.BASE}/ticker/price",
                        params={"symbol": f"{sym.upper()}USDT"},
                    )
                    resp.raise_for_status()
                    row = resp.json()
                    if isinstance(row, list):
                        row = row[0] if row else None
                    if row and "price" in row:
                        out[sym] = {"quote": {convert: {"price": float(row["price"])}}}
                except Exception as inner:
                    log.debug("binance: skip %s (%s)", sym, inner)
                    continue
        return {"data": out, "status": {"error_code": 0, "note": "binance"}}

    async def ohlcv_historical(
        self, symbols: list[str], time_period: str = "hour",
        count: int = 24, convert: str = "USD",
    ) -> dict:
        interval = {"hour": "1h", "day": "1d", "minute": "1m"}.get(time_period, "1h")
        out = {}
        for sym in symbols:
            # v2.1.7: per-symbol try/except. Symbols that don't exist on
            # Binance (e.g. small-cap BEP-20s not listed there) used to
            # crash the whole batch via raise_for_status(), which the
            # sleeves caught and returned no signals. With the hybrid
            # x402+Binance setup we always pass a basket of 20+ symbols;
            # silently dropping the ones Binance doesn't know is the
            # right behavior.
            try:
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
                #
                # v2.1.8 (F2): cast OHLCV numeric fields to float. Binance
                # returns each field as a JSON string; sleeve C does
                # `quotes[-1]["close"] - quotes[-2]["close"]` directly and
                # raised `TypeError: unsupported operand type(s) for -: 'str'
                # and 'str'`. CMCProClient + MockClient already return floats;
                # casting here makes the contract uniform.
                out[sym] = {
                    "quotes": [
                        {
                            "open": float(r[1]), "high": float(r[2]),
                            "low": float(r[3]), "close": float(r[4]),
                            "volume": float(r[5]),
                            "open_time": r[0], "close_time": r[6],
                        }
                        for r in rows
                    ]
                }
            except Exception as e:
                log.debug("binance: skip %s (%s)", sym, e)
                continue
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
