"""CoinMarketCap Agent Hub client.

Uses Data API (REST) + Data MCP, paid per-request via x402.
In replay/test mode, falls back to a deterministic fixture so tests are reproducible.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from decimal import Decimal
from typing import Any

import httpx
import yaml

from .x402 import X402Required, x402_pay

log = logging.getLogger(__name__)


class CMCClient:
    """Async client for CoinMarketCap Agent Hub."""

    def __init__(
        self,
        x402_base: str,
        api_key: str = "",
        mode: str = "testnet",
        wallet=None,
        replay_tape: list[dict] | None = None,
    ):
        self.x402_base = x402_base.rstrip("/")
        self.api_base = "https://pro-api.coinmarketcap.com"
        self.api_key = api_key
        self.mode = mode
        self.wallet = wallet
        self.replay_tape = replay_tape or []
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))
        self._x402_spend_today_usdc = Decimal("0")
        self._x402_spend_day = self._today()
        self._calls: list[dict] = []   # ledger for dashboard / demo

    # --- ledger helpers (visible in dashboard) ---

    @property
    def spend_today(self) -> Decimal:
        if self._today() != self._x402_spend_day:
            self._x402_spend_day = self._today()
            self._x402_spend_today_usdc = Decimal("0")
        return self._x402_spend_today_usdc

    @property
    def calls(self) -> list[dict]:
        return list(self._calls)

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

    # --- core call ---

    async def call(self, method: str, path: str, params: dict | None = None) -> dict:
        """Hit the CMC x402 endpoint. On 402, sign USDC and retry.

        In replay mode, returns the next fixture entry without making any network call.
        """
        params = params or {}

        if self.mode == "replay":
            if not self.replay_tape:
                raise RuntimeError("replay mode requested but no tape loaded")
            entry = self.replay_tape.pop(0)
            return entry

        url = f"{self.x402_base}{path}"
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-CMC_PRO_API_KEY"] = self.api_key

        # first attempt
        resp = await self._client.request(method, url, params=params, headers=headers)
        if resp.status_code != 402:
            resp.raise_for_status()
            return resp.json()

        # 402 → x402 payment
        try:
            payment_hdr = await x402_pay(
                required_b64=resp.headers.get("X-PAYMENT-REQUIRED", ""),
                wallet=self.wallet,
            )
        except X402Required as e:
            log.error("x402 payment failed: %s", e)
            raise

        # record microcharge
        cost = Decimal("0.01")
        self._x402_spend_today_usdc += cost
        self._calls.append({
            "ts": int(time.time()),
            "method": method,
            "path": path,
            "params": params,
            "cost_usdc": str(cost),
            "payment_header": payment_hdr[:64] + "...",
            "status": 200,
        })

        # retry with X-PAYMENT header
        headers["X-PAYMENT"] = payment_hdr
        resp2 = await self._client.request(method, url, params=params, headers=headers)
        resp2.raise_for_status()
        return resp2.json()

    # --- typed helpers used by strategies ---

    async def quotes_latest(self, symbols: list[str], convert: str = "USD") -> dict:
        return await self.call(
            "GET", "/v1/cryptocurrency/quotes/latest",
            {"symbol": ",".join(symbols), "convert": convert},
        )

    async def ohlcv_historical(
        self, symbols: list[str], time_period: str = "hour", count: int = 24, convert: str = "USD"
    ) -> dict:
        return await self.call(
            "GET", "/v1/cryptocurrency/ohlcv/historical",
            {
                "symbol": ",".join(symbols),
                "time_period": time_period,
                "count": count,
                "convert": convert,
            },
        )

    async def global_metrics(self) -> dict:
        return await self.call("GET", "/v1/global-metrics/quotes/latest")

    async def fear_and_greed(self) -> dict:
        return await self.call("GET", "/v3/fear-and-greed/latest")

    async def dex_listings(self, limit: int = 100) -> dict:
        return await self.call("GET", "/v4/dex/listings/quotes", {"limit": limit})

    async def exchange_listings(self, limit: int = 100) -> dict:
        return await self.call("GET", "/v1/exchange/listings/latest", {"limit": limit})

    # --- convenience: cmc_rank ---

    async def cmc_rank_map(self) -> dict[str, int]:
        """Return {symbol: cmc_rank} for top-200."""
        r = await self.call("GET", "/v1/cryptocurrency/map", {"limit": 200})
        return {row["symbol"]: row["rank"] for row in r.get("data", [])}

    async def close(self):
        await self._client.aclose()


def from_config(path: str = "config/config.yaml", wallet=None) -> CMCClient:
    cfg = yaml.safe_load(open(path))
    return CMCClient(
        x402_base=cfg["cmc"]["x402_base"],
        api_key=cfg["cmc"].get("api_key", ""),
        mode=cfg.get("mode", "testnet"),
        wallet=wallet,
    )
