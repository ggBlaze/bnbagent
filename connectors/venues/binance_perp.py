"""Binance USDT-margined futures read-only client.

v2.3.5 (Option B): paper perps on BSC mainnet + real mark + funding
sourced from Binance Futures public REST API. No auth required for the
endpoints used here. The full URL list is at
https://binance-docs.github.io/apidocs/futures/en/

Why this exists:
  - BSC perps venues (Aster, KiloEx, ApolloX, MUX) on mainnet each
    have their own order endpoints that aren't implemented in this
    codebase yet. Until they are, the agent can't actually trade
    perps — only paper-trade them.
  - But sleeve A still needs REAL mark price (for basis math) and
    REAL funding rate (for the carry signal). A canned stub would
    mislead the strategy into thinking $ETH trades at $100 with
    0.0005 funding per 8h.
  - Binance Futures is the deepest USDT-margined venue globally.
    Its public mark + funding endpoints are stable, fast, and
    don't need an API key for the read paths we use. So we use
    Binance as the price oracle while keeping BSC on-chain as the
    spot leg's settlement venue.

Endpoints used:
  - GET https://fapi.binance.com/fapi/v1/premiumIndex
      → {"symbol":"BTCUSDT","markPrice":"65000.00","...","time":...}
  - GET https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT
      → [{"symbol":"BTCUSDT","fundingRate":"0.0001","fundingTime":...}]

Symbol convention: BSC uses bare symbols ("ETH", "BTC"). Binance uses
USDT-paired perps ("ETHUSDT", "BTCUSDT"). The mapping is mechanical:
  ETH  → ETHUSDT
  BTC  → BTCUSDT
  CAKE → CAKEUSDT  (Binance does not list Cake perps → returns None
                    on the read; sleeve A falls back to spot math)

The client implements the same BaseVenueClient interface as the
paper-stub and live venues, so it slots into Perps._resolve_client.
For "paper_perps=True" mode, we use this client to GET price data
but still route orders through PaperStubClient (so the on-chain
trade leg stays paper while the read path is live).
"""
from __future__ import annotations

import logging
import time as _time
from decimal import Decimal
from typing import Optional

from .base import (
    BaseVenueClient,
    VenueOrderError,
    VenueOrderResult,
    VenuePosition,
)

log = logging.getLogger(__name__)

BINANCE_FAPI_BASE = "https://fapi.binance.com"

# Symbol → Binance USDT-pair conversion. If a symbol has no perp on
# Binance, the lookup returns None and the caller falls back to the
# Perps.mark() stub orbs the historical stub.
# The :USDT suffix covers ~95% of CMC top-50 majors. Tokens without
# USDT perps on Binance are skipped silently.
_BINANCE_QUOTE = "USDT"

# Minimal symbol set so we don't make per-symbol HTTP calls for every
# token sleeve A scans. The mark() HTTP cache handles this anyway.
_SYMBOL_MAP_CACHE: dict[str, Optional[str]] = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
    "DOGE": "DOGEUSDT",
    "ADA": "ADAUSDT",
    "AVAX": "AVAXUSDT",
    "LINK": "LINKUSDT",
    "DOT": "DOTUSDT",
    "MATIC": "MATICUSDT",
    "SHIB": "SHIBUSDT",
    "LTC": "LTCUSDT",
    "BCH": "BCHUSDT",
    "NEAR": "NEARUSDT",
    "ATOM": "ATOMUSDT",
    "UNI": "UNIUSDT",
    "APT": "APTUSDT",
    "AAVE": "AAVEUSDT",
    "INJ": "INJUSDT",
    "TRX": "TRXUSDT",
    "FIL": "FILUSDT",
    "TON": "TONUSDT",
    "DAI": "DAIUSDT",
    "TUSD": "TUSDUSDT",
    # WBNB / USDC / USDT / CAKE / BTCB have no Binance USDT perp.
}


def _to_binance_symbol(symbol: str) -> Optional[str]:
    """Translate a BSC-style symbol to Binance USDT perp symbol, or None."""
    s = (symbol or "").upper().strip()
    if not s:
        return None
    if s in _SYMBOL_MAP_CACHE:
        return _SYMBOL_MAP_CACHE[s]
    # Default: append USDT. Caller will get None on HTTP 400 if the
    # pair doesn't exist on Binance.
    return f"{s}{_BINANCE_QUOTE}"


class BinanceFuturesReadOnlyClient(BaseVenueClient):
    """Read-only Binance USDT-margined futures client.

    Implements the BaseVenueClient interface but ONLY read methods
    work. place_order / close_position / reduce_position raise
    VenueOrderError so a misconfigured caller can't accidentally send
    a real trade (the agent is in paper-perps mode by design; if the
    caller wants to actually trade, they should register a real
    venue client in VenueRegistry instead).
    """

    venue_name = "binance_perp_ro"

    # Mark + funding caches. Keyed by BSC symbol. The Perps class
    # already has a 60s mark cache; this internal cache is a
    # second layer so we don't hammer Binance on every tick.
    _mark_cache: dict[str, tuple[float, float]] = {}    # sym -> (mark, fetched_at)
    _funding_cache: dict[str, tuple[float, float]] = {} # sym -> (rate, fetched_at)
    _CACHE_TTL_S = 30.0

    def __init__(self, config: dict | None = None):
        super().__init__(config or {})
        # Read paths are public — no api_key/api_secret needed.

    # --- read paths: the whole reason this client exists ---

    def get_mark_price(self, symbol: str) -> Optional[Decimal]:
        """Live mark price from /fapi/v1/premiumIndex.

        Returns None on any error so the caller can fall back. The
        Perps.mark() chain has its own cache + stub fallback, so this
        None just means "skip the live read, use the next-best path".
        """
        binance_sym = _to_binance_symbol(symbol)
        if not binance_sym:
            return None

        now = _time.time()
        cached = self._mark_cache.get(symbol)
        if cached and (now - cached[1]) < self._CACHE_TTL_S:
            return Decimal(str(cached[0]))

        try:
            url = f"{BINANCE_FAPI_BASE}/fapi/v1/premiumIndex?symbol={binance_sym}"
            status, body = self._http_get(url, timeout=3.0)
            if status != 200 or not isinstance(body, dict):
                log.warning("binance_perp_ro: premiumIndex %s status=%s body=%s",
                            binance_sym, status, body)
                return None
            mark_str = body.get("markPrice")
            if mark_str is None:
                return None
            mark = float(mark_str)
            self._mark_cache[symbol] = (mark, now)
            return Decimal(str(mark))
        except Exception as e:
            log.warning("binance_perp_ro: mark fetch failed for %s: %s", symbol, e)
            return None

    def fetch_funding_rate(self, symbol: str) -> Optional[float]:
        """Most recent funding rate from /fapi/v1/fundingRate.

        Returns the rate as a fraction (e.g., 0.0001 = 1bp). Binance
        settles every 8h; the latest settled rate is what we return.
        Returns None on any error.
        """
        binance_sym = _to_binance_symbol(symbol)
        if not binance_sym:
            return None

        now = _time.time()
        cached = self._funding_cache.get(symbol)
        if cached and (now - cached[1]) < self._CACHE_TTL_S:
            return cached[0]

        try:
            url = f"{BINANCE_FAPI_BASE}/fapi/v1/fundingRate?symbol={binance_sym}&limit=1"
            status, body = self._http_get(url, timeout=3.0)
            if status != 200 or not isinstance(body, list) or not body:
                log.warning("binance_perp_ro: fundingRate %s status=%s body=%s",
                            binance_sym, status, body)
                return None
            rate_str = body[0].get("fundingRate")
            if rate_str is None:
                return None
            rate = float(rate_str)
            self._funding_cache[symbol] = (rate, now)
            return rate
        except Exception as e:
            log.warning("binance_perp_ro: funding fetch failed for %s: %s", symbol, e)
            return None

    # --- write paths: disabled. The agent in paper_perps mode must
    #     route orders through PaperStubClient. If you reach these
    #     methods, your call site is wrong — fix it, don't bypass. ---

    def place_order(
        self, symbol, side, size_usd, leverage, collateral_usdc, *,
        client_order_id=None,
    ) -> VenueOrderResult:
        raise VenueOrderError(
            "binance_perp_ro is read-only — use PaperStubClient (paper_perps=true) "
            "or register a real Binance execution client in VenueRegistry"
        )

    def close_position(self, symbol, *, venue_order_id=None) -> VenueOrderResult:
        raise VenueOrderError("binance_perp_ro is read-only")

    def reduce_position(self, symbol, factor, *, venue_order_id=None) -> VenueOrderResult:
        raise VenueOrderError("binance_perp_ro is read-only")

    def get_position(self, symbol) -> Optional[VenuePosition]:
        # We don't track positions (the on-chain wallet or the paper
        # simulator does). Return None so the caller falls through.
        return None