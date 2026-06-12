"""CoinMarketCap data sources.

Two implementations of MarketDataSource:

  - CMCProClient   — paid Pro API. Auth via X-CMC_PRO_API_KEY header.
                      Base: https://pro-api.coinmarketcap.com/v1/...
  - CMCX402Client  — x402 pay-per-request. Auth via EIP-3009 over Base USDC.
                      Base: https://pro-api.coinmarketcap.com/x402/...
                      Signs payments via connectors.x402.x402_pay.
"""
from __future__ import annotations

import logging
import time
from decimal import Decimal

import httpx

from .x402 import X402Required, x402_pay

log = logging.getLogger(__name__)


# --- Common: ledger, spend tracking ---

class _LedgerMixin:
    """Tracks daily x402 spend + per-call ledger for the dashboard."""

    def __init__(self):
        self._x402_spend_today_usdc = Decimal("0")
        self._x402_spend_day = self._today()
        self._calls: list[dict] = []

    @staticmethod
    def _today() -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

    @property
    def spend_today(self) -> Decimal:
        if self._today() != self._x402_spend_day:
            self._x402_spend_day = self._today()
            self._x402_spend_today_usdc = Decimal("0")
        return self._x402_spend_today_usdc

    @property
    def calls(self) -> list[dict]:
        return list(self._calls)

    def _record(self, method, path, params, cost, status):
        self._calls.append({
            "ts": int(time.time()),
            "method": method,
            "path": path,
            "params": params,
            "cost_usdc": str(cost),
            "status": status,
        })


# --- CMCProClient ---

class CMCProClient(_LedgerMixin):
    """Pro API at https://pro-api.coinmarketcap.com — paid subscription."""

    BASE = "https://pro-api.coinmarketcap.com"

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None):
        super().__init__()
        self.api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))

    @property
    def tier(self) -> str:
        return "cmc_pro"

    @property
    def status(self) -> dict:
        return {"tier": "cmc_pro", "api_key_set": bool(self.api_key)}

    async def close(self):
        await self._client.aclose()

    async def _call(self, method: str, path: str, params: dict | None = None) -> dict:
        params = params or {}
        headers = {"Accept": "application/json", "X-CMC_PRO_API_KEY": self.api_key}
        resp = await self._client.request(method, self.BASE + path, params=params, headers=headers)
        if resp.status_code != 200:
            self._record(method, path, params, Decimal("0"), resp.status_code)
            body = {}
            try:
                body = resp.json()
            except Exception:
                pass
            err_code = body.get("status", {}).get("error_code", 0) if isinstance(body, dict) else 0
            err_msg = body.get("status", {}).get("error_message", resp.text) if isinstance(body, dict) else resp.text
            raise RuntimeError(f"CMC Pro API error {resp.status_code} (code {err_code}): {err_msg}")
        self._record(method, path, params, Decimal("0"), 200)
        return resp.json()

    async def quotes_latest(self, symbols: list[str], convert: str = "USD") -> dict:
        return await self._call("GET", "/v1/cryptocurrency/quotes/latest",
                                 {"symbol": ",".join(symbols), "convert": convert})

    async def ohlcv_historical(
        self, symbols: list[str], time_period: str = "hour", count: int = 24, convert: str = "USD"
    ) -> dict:
        return await self._call("GET", "/v1/cryptocurrency/ohlcv/historical", {
            "symbol": ",".join(symbols), "time_period": time_period,
            "count": count, "convert": convert,
        })

    async def cmc_rank_map(self) -> dict[str, int]:
        r = await self._call("GET", "/v1/cryptocurrency/map", {"limit": 200})
        return {row["symbol"]: row["rank"] for row in r.get("data", [])}

    async def global_metrics(self) -> dict:
        return await self._call("GET", "/v1/global-metrics/quotes/latest")

    async def fear_and_greed(self) -> dict:
        return await self._call("GET", "/v3/fear-and-greed/latest")

    async def dex_listings(self, limit: int = 100) -> dict:
        return await self._call("GET", "/v4/dex/listings/quotes", {"limit": limit})

    async def exchange_listings(self, limit: int = 100) -> dict:
        return await self._call("GET", "/v1/exchange/listings/latest", {"limit": limit})


# --- CMCX402Client ---

class CMCX402Client(_LedgerMixin):
    """x402 pay-per-request at https://pro-api.coinmarketcap.com/x402/...

    Signs EIP-3009 transferWithAuthorization over Base USDC and retries
    the request with a PAYMENT-SIGNATURE header. Daily spend is capped
    via the daily_cap_usdc parameter; calls beyond the cap raise.
    """

    BASE = "https://pro-api.coinmarketcap.com"

    def __init__(
        self,
        wallet,
        base_rpcs: list[str] | None = None,
        chain_id: int = 8453,
        token_address: str = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        daily_cap_usdc: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ):
        super().__init__()
        self.wallet = wallet
        self.base_rpcs = base_rpcs or [
            "https://mainnet.base.org",
            "https://base.publicnode.com",
            "https://1rpc.io/base",
        ]
        self.chain_id = chain_id
        self.token_address = token_address
        self.daily_cap_usdc = Decimal(str(daily_cap_usdc))
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))

    @property
    def tier(self) -> str:
        return "x402"

    @property
    def status(self) -> dict:
        return {
            "tier": "x402",
            "chain_id": self.chain_id,
            "token": self.token_address,
            "base_rpcs": self.base_rpcs,
            "spend_today_usdc": str(self.spend_today),
            "daily_cap_usdc": str(self.daily_cap_usdc),
        }

    async def close(self):
        await self._client.aclose()

    async def _call(self, method: str, path: str, params: dict | None = None) -> dict:
        params = params or {}
        if self.spend_today >= self.daily_cap_usdc:
            self._record(method, path, params, Decimal("0"), 429)
            raise RuntimeError(f"x402 daily cap reached: {self.daily_cap_usdc} USDC")

        url = self.BASE + path
        headers = {"Accept": "application/json"}
        resp = await self._client.request(method, url, params=params, headers=headers)

        if resp.status_code != 402:
            if resp.status_code != 200:
                self._record(method, path, params, Decimal("0"), resp.status_code)
                resp.raise_for_status()
            self._record(method, path, params, Decimal("0"), 200)
            return resp.json()

        # 402 → sign + retry
        try:
            payment_hdr = await x402_pay(
                required_b64=resp.headers.get("PAYMENT-REQUIRED", ""),
                wallet=self.wallet,
                chain_id=self.chain_id,
            )
        except X402Required as e:
            self._record(method, path, params, Decimal("0"), 402)
            raise

        cost = Decimal("0.01")
        self._x402_spend_today_usdc += cost
        headers["PAYMENT-SIGNATURE"] = payment_hdr
        resp2 = await self._client.request(method, url, params=params, headers=headers)
        self._record(method, path, params, cost, resp2.status_code)
        resp2.raise_for_status()
        return resp2.json()

    async def quotes_latest(self, symbols: list[str], convert: str = "USD") -> dict:
        return await self._call("GET", "/x402/v3/cryptocurrency/quotes/latest",
                                 {"symbol": ",".join(symbols), "convert": convert})

    async def ohlcv_historical(
        self, symbols: list[str], time_period: str = "hour", count: int = 24, convert: str = "USD"
    ) -> dict:
        raise NotImplementedError("x402 has no OHLCV endpoint — falling back to mock")

    async def cmc_rank_map(self) -> dict[str, int]:
        r = await self._call("GET", "/x402/v3/cryptocurrency/listings/latest", {"limit": 200})
        return {row["symbol"]: idx + 1 for idx, row in enumerate(r.get("data", []))}

    async def global_metrics(self) -> dict:
        raise NotImplementedError("x402 has no global metrics endpoint — falling back to mock")

    async def fear_and_greed(self) -> dict:
        raise NotImplementedError("x402 has no fear & greed endpoint — falling back to mock")

    async def dex_listings(self, limit: int = 100) -> dict:
        return await self._call("GET", "/x402/v1/dex/search", {"limit": limit})

    async def exchange_listings(self, limit: int = 100) -> dict:
        return await self._call("GET", "/x402/v4/dex/pairs/quotes/latest", {"limit": limit})


# --- Back-compat shim ---
#
# The old single-class CMCClient is removed in this commit. core/boot.py
# and backtest/fetch_history.py still import the old name; this shim
# preserves the old construction signature so those modules keep
# importing and the agent stays bootable. Task 3 will rewire boot.py
# to use DataSourceRouter + CMCProClient/CMCX402Client directly.

class CMCClient:  # pragma: no cover — back-compat only, replaced in Task 3
    """Deprecated. Use CMCProClient (paid) or CMCX402Client (pay-per-req)."""

    def __init__(
        self,
        x402_base: str,
        api_key: str = "",
        mode: str = "testnet",
        wallet=None,
        replay_tape: list | None = None,
    ):
        import warnings
        warnings.warn(
            "CMCClient is deprecated; use CMCProClient or CMCX402Client",
            DeprecationWarning,
            stacklevel=2,
        )
        # Pick the right new class based on the legacy config (api_key set → Pro).
        if api_key:
            self._impl = CMCProClient(api_key=api_key)
        else:
            self._impl = CMCX402Client(
                wallet=wallet,
                base_rpcs=[x402_base] if x402_base else None,
            )
        self.x402_base = x402_base
        self.mode = mode
        self.replay_tape = replay_tape or []

    @property
    def tier(self) -> str:
        return self._impl.tier

    @property
    def status(self) -> dict:
        return self._impl.status

    @property
    def spend_today(self):
        return self._impl.spend_today

    @property
    def calls(self):
        return self._impl.calls

    async def call(self, method: str, path: str, params: dict | None = None) -> dict:
        return await self._impl._call(method, path, params)

    async def quotes_latest(self, symbols: list[str], convert: str = "USD") -> dict:
        return await self._impl.quotes_latest(symbols, convert)

    async def ohlcv_historical(
        self, symbols: list[str], time_period: str = "hour", count: int = 24, convert: str = "USD"
    ) -> dict:
        return await self._impl.ohlcv_historical(symbols, time_period, count, convert)

    async def global_metrics(self) -> dict:
        return await self._impl.global_metrics()

    async def fear_and_greed(self) -> dict:
        return await self._impl.fear_and_greed()

    async def dex_listings(self, limit: int = 100) -> dict:
        return await self._impl.dex_listings(limit)

    async def exchange_listings(self, limit: int = 100) -> dict:
        return await self._impl.exchange_listings(limit)

    async def cmc_rank_map(self) -> dict[str, int]:
        return await self._impl.cmc_rank_map()

    async def close(self):
        await self._impl.close()
