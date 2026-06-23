"""CoinMarketCap data sources.

Two implementations of MarketDataSource:

  - CMCProClient   — paid Pro API. Auth via X-CMC_PRO_API_KEY header.
                      Base: https://pro-api.coinmarketcap.com/v1/...
  - CMCX402Client  — x402 pay-per-request. Auth via EIP-3009 over Base USDC.
                      Base: https://pro-api.coinmarketcap.com/x402/...
                      Signs payments via connectors.x402.x402_pay.
"""
from __future__ import annotations

import json
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
        raw = await self._call("GET", "/v1/cryptocurrency/ohlcv/historical", {
            "symbol": ",".join(symbols), "time_period": time_period,
            "count": count, "convert": convert,
        })
        # Normalize CMC's nested {quote: {USD: {open, high, low, close, volume}}}
        # to the flat shape strategies expect: {quotes: [{open, high, low, close,
        # volume, ...}, ...]}. Same flat shape as MockClient + BinanceClient
        # (post-fix). Without this normalization, strategies read
        # payload['quotes'][i]['close'] and get KeyError because each candle's
        # close is nested under quote.USD.
        data = raw.get("data", {})
        for sym, entry in data.items():
            quotes_raw = entry.get("quotes", [])
            flat = []
            for q in quotes_raw:
                usd = (q.get("quote") or {}).get(convert) or (q.get("quote") or {}).get("USD") or {}
                flat.append({
                    "timestamp":   q.get("timestamp"),
                    "time_open":   q.get("time_open"),
                    "time_close":  q.get("time_close"),
                    "open":        usd.get("open"),
                    "high":        usd.get("high"),
                    "low":         usd.get("low"),
                    "close":       usd.get("close"),
                    "volume":      usd.get("volume"),
                })
            entry["quotes"] = flat
        return raw

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

        # 402 → sign + retry. Parse the 402 challenge FIRST so we can sign
        # for the actual chain/token the server is asking for and use the
        # actual cost in the ledger. v2.1.8 (x402-fix): two bugs were
        # observed live against the CMC x402 endpoint:
        #   1. The EIP-712 domain used the configured defaults (Base/USDC)
        #      while CMC was asking for a BSC token, so the facilitator
        #      rejected every signed authorization and returned 402 again.
        #   2. The spend counter was incremented the moment the signature
        #      was built, regardless of whether the retry settled. So 1000
        #      failed attempts of 0.01 USDC = "spent: 10 USDC" in the
        #      ledger, even though zero actually moved.
        # Both fixed here: derive chain_id/token from `req`, and only
        # count spend on a successful (200) retry.
        #
        # v2.2.2 (x402-payable-accept): CMC offers 6+ accepts per
        # 402 — 6 on BSC and 1 on Base at the time of writing. The
        # first accept is usually a BSC alt-stable (United Stables,
        # World Liberty Financial USD) that the agent has 0 of. The
        # previous code blindly signed for accepts[0], the signed
        # authorization settled against an empty balance, and the
        # server returned 402 forever. The fix: walk the accepts
        # list, query the wallet's balance for each token on its
        # network, and pick the first one with sufficient balance.
        # If none of the accepts are payable, raise so the caller
        # can fall back to the mock data source.
        import base64 as _b64
        from .x402 import decode_payment_requirements, pick_payable_accept, _parse_chain_id
        try:
            raw_b64 = resp.headers.get("PAYMENT-REQUIRED", "")
            raw = _b64.b64decode(raw_b64)
            d402 = json.loads(raw)
            accepts = d402.get("accepts") or []
            # Try to find a payable accept that matches the wallet.
            chosen = None
            if accepts and isinstance(accepts, list):
                chosen = await pick_payable_accept(accepts, self.wallet.address)
            if chosen is None:
                # No accept is payable — fail loudly so the caller
                # falls back to mock instead of signing for a token
                # we don't hold.
                self._record(method, path, params, Decimal("0"), 402)
                raise RuntimeError(
                    f"x402: none of the {len(accepts)} accepts are payable "
                    f"by wallet {self.wallet.address} (no token balance on "
                    f"the requested chains)"
                )
            # Build a synthetic PAYMENT-REQUIRED header for the chosen
            # accept so decode_payment_requirements + x402_pay can sign
            # against it directly. Preserve the outer `resource` field
            # AND the inner `maxTimeoutSeconds`/`extra` because x402 V2
            # servers require all of these in the PAYMENT-SIGNATURE
            # payload (or the response is 'payment header resource is null'
            # or 'permit2 authorization or witness is null').
            chosen_b64 = _b64.b64encode(
                json.dumps({
                    "x402Version": 2,
                    "resource":    d402.get("resource", {}),
                    "accepts":     [chosen],
                }).encode()
            ).decode()
            req = decode_payment_requirements(chosen_b64)
            cost = Decimal(req.amount) / Decimal(10 ** 6)  # USDC has 6 decimals
            network_chain_id = _parse_chain_id(req.network) or self.chain_id
            payment_token = req.token or self.token_address
            log.info(
                "x402 payable accept picked: chain=%d token=%s amount=%s (wallet %s)",
                network_chain_id, payment_token, req.amount, self.wallet.address,
            )
        except Exception as e:
            # If the balance-checked path itself blew up (RPC flake,
            # etc.), fall back to the legacy single-accept path. The
            # legacy path may also fail, but at least we tried.
            if "x402: none of the" in str(e):
                raise  # propagate the explicit "no payable accept" error
            try:
                req = decode_payment_requirements(resp.headers.get("PAYMENT-REQUIRED", ""))
                cost = Decimal(req.amount) / Decimal(10 ** 6)
                if req.network.startswith("eip155:"):
                    network_chain_id = int(req.network.split(":", 1)[1])
                elif req.network == "bsc":
                    network_chain_id = 56
                elif req.network == "base":
                    network_chain_id = 8453
                else:
                    network_chain_id = self.chain_id
                payment_token = req.token or self.token_address
            except Exception:
                # Unparseable challenge → sign for the configured default
                # and use a $0.01 fallback cost (current x402 price floor).
                cost = Decimal("0.01")
                network_chain_id = self.chain_id
                payment_token = self.token_address

        try:
            payment_hdr = await x402_pay(
                required_b64=chosen_b64,
                wallet=self.wallet,
                chain_id=network_chain_id,
                token_address=payment_token,
            )
        except X402Required as e:
            self._record(method, path, params, Decimal("0"), 402)
            raise

        headers["PAYMENT-SIGNATURE"] = payment_hdr
        resp2 = await self._client.request(method, url, params=params, headers=headers)
        # Only count spend on a successful retry. A rejected signature
        # (402 again), 4xx/5xx, or network error → no money moved → the
        # cap stays where it was. The cap is a safety limit on real
        # settlement, not on signature attempts.
        if resp2.status_code != 200:
            self._record(method, path, params, Decimal("0"), resp2.status_code)
            resp2.raise_for_status()
        self._x402_spend_today_usdc += cost
        self._record(method, path, params, cost, 200)
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
