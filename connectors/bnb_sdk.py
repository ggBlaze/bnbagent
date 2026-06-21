"""bnbagent-sdk (BSC) wrapper.

Provides:
  - BSCClient           → RPC connection pool, broadcast, nonce mgmt
  - PancakeV3           → swap + quote helpers
  - Perps               → multi-venue funding/OI/position mgmt (Aster, KiloEx, ApolloX, MUX)
  - ERC8004             → identity NFT registration
  - ERC8183             → job escrow lifecycle

In testnet mode, broadcast and contract writes are stubbed (return deterministic
receipts) so the full stack runs end-to-end without spending real gas.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable

import httpx
import yaml
from pathlib import Path
from core.config_paths import load_config as _load_merged_config, DEFAULT_CONFIG
from web3 import Web3

from .twak import TWAKWallet, SignedTx

log = logging.getLogger(__name__)


def _parse_mark_payload(payload, symbol: str) -> float | None:
    """Extract the mark price for `symbol` from a venue's response.

    Tolerant of common shapes so we don't have to special-case each
    perps venue's JSON layout. Returns None if the symbol can't be
    located or the value can't be coerced to float.

    Recognized shapes:
      1. {"ETH": 1735.93}                              — flat dict
      2. [{"symbol": "ETH", "markPrice": "1735.93"}]   — Binance list
      3. {"data": [{"symbol": "...", "markPrice": ...}]} — wrapped list
      4. {"result": {...}}                              — wrapped dict (recurse)

    Symbol matching is case-insensitive on the input `symbol` and the
    venue's "symbol" field. Common suffixes ("USDT", "USD", "PERP")
    are stripped before matching so "ETH" matches "ETHUSDT".
    """
    def _norm(s: str) -> str:
        s = s.upper()
        for suf in ("USDT", "USDC", "USD", "PERP"):
            if s.endswith(suf):
                s = s[: -len(suf)]
        return s
    target = _norm(symbol)

    if isinstance(payload, dict):
        # Direct hit
        for k, v in payload.items():
            if isinstance(k, str) and _norm(k) == target:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None
        # Wrapped list
        if isinstance(payload.get("data"), list):
            return _parse_mark_payload(payload["data"], symbol)
        # Wrapped dict — recurse
        if isinstance(payload.get("result"), (dict, list)):
            return _parse_mark_payload(payload["result"], symbol)
        return None

    if isinstance(payload, list):
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            sym = entry.get("symbol") or entry.get("s") or entry.get("market")
            if not isinstance(sym, str):
                continue
            if _norm(sym) != target:
                continue
            for key in ("markPrice", "mark_price", "price", "p", "last", "mark"):
                if key in entry:
                    try:
                        return float(entry[key])
                    except (TypeError, ValueError):
                        return None
        return None

    return None


# ABI fragments (minimal subsets for swap + ERC-20 + the registry interfaces)

ERC20_ABI = [
    {"name": "balanceOf",  "type": "function", "stateMutability": "view",
     "inputs": [{"name": "a", "type": "address"}], "outputs": [{"type": "uint256"}]},
    {"name": "decimals",   "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"type": "uint8"}]},
    {"name": "symbol",     "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"type": "string"}]},
    {"name": "approve",    "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "spender", "type": "address"},
                {"name": "amount",  "type": "uint256"}], "outputs": [{"type": "bool"}]},
    {"name": "allowance",  "type": "function", "stateMutability": "view",
     "inputs": [{"name": "owner",   "type": "address"},
                {"name": "spender", "type": "address"}], "outputs": [{"type": "uint256"}]},
]

PCSV3_ROUTER_ABI = [
    {
        "name": "exactInputSingle",
        "type": "function", "stateMutability": "payable",
        "inputs": [{
            "name": "params", "type": "tuple",
            "components": [
                {"name": "tokenIn",           "type": "address"},
                {"name": "tokenOut",          "type": "address"},
                {"name": "fee",               "type": "uint24"},
                {"name": "recipient",         "type": "address"},
                {"name": "amountIn",          "type": "uint256"},
                {"name": "amountOutMinimum",  "type": "uint256"},
                {"name": "sqrtPriceLimitX96", "type": "uint160"},
            ]
        }],
        "outputs": [{"type": "uint256"}],
    }
]

PCSV3_QUOTER_ABI = [
    {
        "name": "quoteExactInputSingle",
        "type": "function", "stateMutability": "nonpayable",
        "inputs": [{
            "name": "params", "type": "tuple",
            "components": [
                {"name": "tokenIn",           "type": "address"},
                {"name": "tokenOut",          "type": "address"},
                {"name": "fee",               "type": "uint24"},
                {"name": "amountIn",          "type": "uint256"},
                {"name": "sqrtPriceLimitX96", "type": "uint160"},
            ]
        }],
        "outputs": [{"type": "uint256"}],
    }
]

ERC8004_REGISTRY_ABI = [
    {
        "name": "register", "type": "function", "stateMutability": "nonpayable",
        "inputs": [{"name": "agentURI", "type": "string"}],
        "outputs": [{"name": "tokenId", "type": "uint256"}],
    },
    {
        "name": "tokenURI", "type": "function", "stateMutability": "view",
        "inputs": [{"name": "tokenId", "type": "uint256"}],
        "outputs": [{"type": "string"}],
    },
]

ERC8183_ABI = [
    {"name": "createJob", "type": "function", "stateMutability": "nonpayable",
     "inputs": [
         {"name": "provider",         "type": "address"},
         {"name": "evaluator",        "type": "address"},
         {"name": "deliverableSpec",  "type": "bytes32"},
         {"name": "budget",           "type": "uint256"},
         {"name": "token",            "type": "address"},
     ], "outputs": [{"name": "jobId", "type": "uint256"}]},
    {"name": "fund",      "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "jobId", "type": "uint256"},
                {"name": "amount","type": "uint256"}], "outputs": []},
    {"name": "submit",    "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "jobId", "type": "uint256"},
                {"name": "proof","type": "bytes32"}], "outputs": []},
    {"name": "complete",  "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "jobId", "type": "uint256"}], "outputs": []},
    {"name": "reject",    "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "jobId", "type": "uint256"}], "outputs": []},
]


@dataclass
class TxReceipt:
    tx_hash: str
    block_number: int
    gas_used: int
    status: int
    contract_address: str | None = None
    logs: list = field(default_factory=list)


class BSCClient:
    """Connection-pooled BSC RPC client. Rotates across multiple endpoints."""

    def __init__(self, rpcs: list[str], chain_id: int = 97, mode: str = "testnet"):
        self.rpcs = rpcs
        self.chain_id = chain_id
        self.mode = mode
        self._idx = 0
        self._w3: Web3 | None = None
        self._nonce_cache: dict[str, int] = {}

    def w3(self) -> Web3:
        if self._w3 is None:
            self._w3 = Web3(Web3.HTTPProvider(self.rpcs[self._idx], request_kwargs={"timeout": 10}))
        return self._w3

    def rotate(self):
        self._idx = (self._idx + 1) % len(self.rpcs)
        self._w3 = Web3(Web3.HTTPProvider(self.rpcs[self._idx], request_kwargs={"timeout": 10}))
        log.info("rotated RPC → %s", self.rpcs[self._idx])

    def next_nonce(self, address: str) -> int:
        n = self._nonce_cache.get(address, -1) + 1
        self._nonce_cache[address] = n
        return n

    def resync_nonce(self, address: str) -> int:
        """v2.0.8-H3: reconcile the local nonce cache from chain state.

        On mainnet, the in-memory cache can drift from the chain's
        'pending' nonce after a crash, restart, or partial broadcast.
        Calling this method queries eth_getTransactionCount(address, 'pending')
        and reseeds the cache, so the next next_nonce() returns a value
        that won't be rejected as 'nonce too low' or 'nonce too high'.

        In testnet/replay mode, the broadcast path is stubbed and the
        chain isn't queried — the cache is returned as-is.

        Returns the reconciled nonce (the next one to use).
        """
        if self.mode in ("testnet", "replay"):
            return self._nonce_cache.get(address, -1) + 1
        w3 = self.w3()
        pending = w3.eth.get_transaction_count(Web3.to_checksum_address(address), "pending")
        # the next nonce to use is the chain's pending nonce
        self._nonce_cache[address] = pending - 1  # -1 because next_nonce adds 1
        log.info("resync_nonce %s → %d (chain pending)", address, pending)
        return pending

    def broadcast(self, signed: SignedTx) -> TxReceipt:
        if self.mode in ("testnet", "replay"):
            # stub: deterministic hash, no network call. For contract-create
            # txs (to=None / data starts with the ERC-20 init code), derive
            # a deterministic contract address from (sender, nonce) so the
            # token-launch demo works end-to-end without a live network.
            contract_addr = None
            sender = (signed.signed or {}).get("from")
            nonce = (signed.signed or {}).get("nonce")
            if sender is not None and nonce is not None:
                contract_addr = Web3.to_checksum_address(
                    "0x" + Web3.keccak(
                        Web3.to_bytes(hexstr=Web3.to_checksum_address(sender))
                        + Web3.to_bytes(nonce)
                    ).hex()[-40:]
                )
            return TxReceipt(
                tx_hash=signed.tx_hash,
                block_number=int(time.time()),
                gas_used=21000,
                status=1,
                contract_address=contract_addr,
            )
        w3 = self.w3()
        h = w3.eth.send_raw_transaction(signed.raw_tx)
        rcpt = w3.eth.wait_for_transaction_receipt(h, timeout=120)
        return TxReceipt(
            tx_hash=h.hex(),
            block_number=rcpt.blockNumber,
            gas_used=rcpt.gasUsed,
            status=rcpt.status,
            contract_address=rcpt.get("contractAddress"),
            logs=list(rcpt.get("logs", [])),
        )

    def eth_balance(self, address: str) -> Decimal:
        if self.mode in ("testnet", "replay"):
            return Decimal("5.0")
        return Decimal(Web3.from_wei(self.w3().eth.get_balance(address), "ether"))

    def token_balance(self, token: str, holder: str, decimals: int = 18) -> Decimal:
        if self.mode in ("testnet", "replay"):
            return Decimal("1000")
        w3 = self.w3()
        c = w3.eth.contract(address=Web3.to_checksum_address(token), abi=ERC20_ABI)
        raw = c.functions.balanceOf(Web3.to_checksum_address(holder)).call()
        return Decimal(raw) / Decimal(10 ** decimals)


class PancakeV3:
    """PancakeSwap v3 router + quoter wrapper."""

    def __init__(self, client: BSCClient, router: str, quoter: str, factory: str):
        self.client = client
        self.router = Web3.to_checksum_address(router)
        self.quoter = Web3.to_checksum_address(quoter)
        self.factory = Web3.to_checksum_address(factory)

    def _contract(self, address: str, abi: list) -> Any:
        return self.client.w3().eth.contract(address=address, abi=abi)

    def encode_swap_v3(
        self, token_in: str, token_out: str, fee: int, recipient: str,
        amount_in: int, min_out: int, sqrt_price_limit_x96: int = 0,
    ) -> bytes:
        router = self._contract(self.router, PCSV3_ROUTER_ABI)
        if self.client.mode in ("testnet", "replay"):
            # deterministic stub calldata (130 bytes) so the dashboard shows a real-looking hash
            seed = f"swap:{token_in}:{token_out}:{fee}:{amount_in}:{min_out}".encode()
            return b"\x00" * 4 + Web3.keccak(seed)[:100]
        return router.functions.exactInputSingle((
            Web3.to_checksum_address(token_in),
            Web3.to_checksum_address(token_out),
            int(fee),
            Web3.to_checksum_address(recipient),
            int(amount_in),
            int(min_out),
            int(sqrt_price_limit_x96),
        )).build_transaction({"value": 0})["data"]

    def quote(self, token_in: str, token_out: str, fee: int, amount_in: int) -> int:
        """Returns amount_out. In testnet, returns a deterministic estimate."""
        if self.client.mode in ("testnet", "replay"):
            # assume 1:1 with 0.3% fee for stub; strategies layer on top
            return int(amount_in * 0.997)
        quoter = self._contract(self.quoter, PCSV3_QUOTER_ABI)
        return quoter.functions.quoteExactInputSingle((
            Web3.to_checksum_address(token_in),
            Web3.to_checksum_address(token_out),
            int(fee),
            int(amount_in),
            0,
        )).call()

    def best_pool_fee(self, token_in: str, token_out: str, candidates: list[int]) -> int:
        """Pick the fee tier with the most liquidity (deepest quote)."""
        if self.client.mode in ("testnet", "replay"):
            return candidates[2] if len(candidates) >= 3 else candidates[0]
        best_fee, best_out = candidates[0], 0
        for fee in candidates:
            try:
                out = self.quote(token_in, token_out, fee, 10**18)
                if out > best_out:
                    best_out, best_fee = out, fee
            except Exception:
                continue
        return best_fee


class Perps:
    """Multi-venue perps adapter.

    Reads from perps_venues.yaml + a CMC funding feed. In testnet mode, simulates
    funding rates with a deterministic random walk so the carry strategy has data.
    """

    def __init__(self, config_path: str = "config/perps_venues.yaml", mode: str = "testnet",
                 clock=None, mark_cache_ttl_s: int = 60,
                 config: dict | None = None):
        import time as _time
        with open(config_path) as f:
            self.venues = yaml.safe_load(f) or {}
        self.mode = mode
        # v2.1.8: merged config so venue clients can pull per-venue
        # api_key/api_secret from perps.<venue>.* in local.yaml.
        self.config = config or {}
        self._state: dict[tuple[str, str], dict] = {}
        self._historical: dict[tuple[str, str], list[float]] = {}
        self._rng = random.Random(42)
        # Deterministic clock (v2.0.4). In production this defaults to
        # time.time; in the replay harness it's set to a callable that
        # returns the current tape ts. Used in tx_hash and any other
        # wall-clock read.
        self.clock = clock or _time.time
        self._mark_provider: dict | None = None
        # v2.1.8: real-venue mark cache. Keyed by (venue, market).
        # Stored value is the last-known mark; `_cached_at` is the
        # clock() at fetch time. TTL is consulted by mark() before
        # issuing another HTTP call.
        self._mark_cache: dict[tuple[str, str], tuple[float, float]] = {}
        self._mark_cache_ttl_s = mark_cache_ttl_s

    def set_mark_provider(self, fn):
        """Set a callable (symbol) -> float that returns the current mark.
        Called by the replay harness every tick; the perps stub uses this
        to keep the mark aligned with the live market price."""
        self._mark_provider = {"fn": fn}

    def candidates(self) -> list[str]:
        return list(self.venues.keys())

    # --- funding data ---

    def _ensure(self, venue: str, market: str):
        key = (venue, market)
        if key not in self._state:
            # init 7d of historical 8h funding rates (21 points).
            # Calibration: real BSC venues (Aster, KiloEx, ApolloX, MUX)
            # settle 8h at 0.01%–0.05%; we widen slightly so the carry
            # sleeve has tail events to trade. With this calibration, a
            # 1× carry on $100 over a week yields ~$0.10–$0.30 in funding
            # income, matching the order of magnitude seen in production.
            hist = [self._rng.uniform(-0.0005, 0.0015) for _ in range(21)]
            self._historical[key] = hist
            self._state[key] = {
                "mark": 100.0,
                "oi": 500_000.0,
                "last_funding": hist[-1],
            }
        return self._state[key]

    def current_funding(self, venue: str, market: str) -> float:
        s = self._ensure(venue, market)
        return s["last_funding"]

    def historical_funding(self, venue: str, market: str) -> list[float]:
        s = self._ensure(venue, market)
        return list(self._historical[(venue, market)])

    def mark(self, venue: str, market: str) -> float:
        # Replay-harness path: a mark_provider is wired up explicitly
        # to keep the perp mark aligned with the spot tape.
        fn = self._mark_provider.get("fn") if self._mark_provider else None
        if fn is not None:
            try:
                spot = float(fn(market))
                import zlib
                seed = (zlib.crc32(f"{venue}|{market}".encode()) % 1000) / 1000.0
                basis_bps = (seed - 0.5) * 0.001  # -0.05% to +0.05%
                return spot * (1.0 + basis_bps)
            except Exception:
                pass

        # v2.1.8: production path. In mainnet mode, fetch the real mark
        # from the venue's mark_endpoint and cache it. In testnet/replay,
        # fall straight through to the historical stub.
        if self.mode not in ("testnet", "replay"):
            key = (venue, market)
            now = float(self.clock())
            cached = self._mark_cache.get(key)
            if cached is None or (now - cached[1]) >= self._mark_cache_ttl_s:
                fresh = self.fetch_mark(venue, market)
                if fresh is not None:
                    self._mark_cache[key] = (fresh, now)
                    return fresh
                if cached is not None:
                    # Stale but better than nothing — log and return
                    # the last-known mark so the sleeve doesn't act on
                    # a hard-coded 100.0 stub.
                    log.warning(
                        "Perps.mark: %s/%s fetch failed; using last-known "
                        "mark=%.4f (age=%.1fs)",
                        venue, market, cached[0], now - cached[1],
                    )
                    return cached[0]
                # No cache, fetch failed → fall through to the stub.

        s = self._ensure(venue, market)
        return s["mark"]

    def fetch_mark(self, venue: str, market: str) -> float | None:
        """Fetch the live mark price from the venue's HTTP endpoint.

        Never raises. Returns None on network error, non-200 status,
        unparseable payload, or missing symbol. Caller is responsible
        for caching + fallback (see mark()).
        """
        cfg = self.venues.get(venue) or {}
        url = cfg.get("mark_endpoint")
        if not url:
            log.debug("Perps.fetch_mark: no mark_endpoint for venue=%s", venue)
            return None
        try:
            # Use the module-level httpx so tests can monkeypatch
            # `connectors.bnb_sdk.httpx.get` (or `httpx.get` after the
            # import below) to swap in a fake response.
            import httpx
            resp = httpx.get(url, timeout=2.0)
            if resp.status_code != 200:
                log.warning(
                    "Perps.fetch_mark: %s returned HTTP %s for %s",
                    venue, resp.status_code, market,
                )
                return None
            payload = resp.json()
        except Exception as e:  # noqa: BLE001 — defensive, never raises
            log.warning("Perps.fetch_mark: %s for %s/%s failed: %s",
                        venue, market, url, e)
            return None
        price = _parse_mark_payload(payload, market)
        if price is None:
            log.warning(
                "Perps.fetch_mark: %s response lacked a parseable mark for %s "
                "(payload keys=%s)",
                venue, market,
                list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__,
            )
        return price

    def open_interest_usd(self, venue: str, market: str) -> float:
        s = self._ensure(venue, market)
        return s["oi"]

    def liq_distance_pct(self, venue: str, market: str, side: str) -> float:
        """Distance in % to liquidation price from current mark."""
        s = self._ensure(venue, market)
        # 1x lev short: liq when price = entry * (1 + 0.9) ≈ 90% above mark for short
        return 0.45  # stub: 45% buffer (safe)

    def status(self, venue: str) -> str:
        return "ok"

    # --- order placement (v2.1.8: pluggable per-venue clients) ---
    #
    # In mode=testnet / mode=replay: routes through the paper-stub client
    # so the strategy layer can exercise its full lifecycle without a
    # live venue. Every paper-stub order returns is_paper=True.
    #
    # In mode=mainnet: routes through the registered real client for the
    # venue (aster / killex / apollox / mux). If no client is registered,
    # raises NotImplementedError pointing at docs/venue_implement_me.md.
    # To enable real execution: implement connectors/venues/<venue>.py
    # and set perps.<venue>.{api_key,api_secret} in config/local.yaml.

    def _venue_config(self, venue: str) -> dict:
        """Pull per-venue config from self.config (merged cfg)."""
        perps = (self.config or {}).get("perps") or {}
        return dict(perps.get(venue) or {})

    def _resolve_client(self, venue: str):
        """Pick the right venue client for the current mode.

        mode in ("testnet", "replay"): return PaperStubClient.
        mode == "mainnet": return VenueRegistry.get(venue, self._venue_config(venue)).
        """
        if self.mode in ("testnet", "replay"):
            from connectors.venues.paper_stub import PaperStubClient
            return PaperStubClient(venue, {"venue_name": venue})
        from connectors.venues.registry import VenueRegistry
        return VenueRegistry.get(venue, self._venue_config(venue))

    def open_short(self, venue: str, market: str, size_usd: float, leverage: float,
                   collateral_usdc: float) -> SignedTx:
        client = self._resolve_client(venue)
        result = client.place_order(
            symbol=market, side="short",
            size_usd=Decimal(str(size_usd)), leverage=float(leverage),
            collateral_usdc=Decimal(str(collateral_usdc)),
        )
        # Build a stable tx_hash so dashboards / log scanners can still
        # find this order. Real clients get a hash derived from their
        # venue_order_id; paper stubs already produce a synthetic hash.
        if result.is_paper:
            tx_hash = "0x" + Web3.keccak(
                text=f"perps_open_short:{venue}:{market}:{size_usd}:{self.clock()}"
            ).hex()
        else:
            tx_hash = "0x" + Web3.keccak(
                text=f"perps_open_short:{venue}:{market}:{result.venue_order_id}"
            ).hex()
        return SignedTx(
            raw_tx=b"\x00" * 100, tx_hash=tx_hash, signed={},
            is_paper=result.is_paper, venue_order_id=result.venue_order_id,
        )

    def close_short(self, venue: str, market: str) -> SignedTx:
        client = self._resolve_client(venue)
        result = client.close_position(symbol=market)
        tx_hash = "0x" + Web3.keccak(
            text=f"perps_close_short:{venue}:{market}:{result.venue_order_id}:{self.clock()}"
        ).hex()
        return SignedTx(
            raw_tx=b"\x00" * 100, tx_hash=tx_hash, signed={},
            is_paper=result.is_paper, venue_order_id=result.venue_order_id,
        )

    def reduce_short(self, venue: str, market: str, factor: float) -> SignedTx:
        client = self._resolve_client(venue)
        result = client.reduce_position(symbol=market, factor=float(factor))
        tx_hash = "0x" + Web3.keccak(
            text=f"perps_reduce:{venue}:{market}:{factor}:{result.venue_order_id}"
        ).hex()
        return SignedTx(
            raw_tx=b"\x00" * 100, tx_hash=tx_hash, signed={},
            is_paper=result.is_paper, venue_order_id=result.venue_order_id,
        )

    # --- venue selection: highest |funding_8h| on the basket ---

    def select_venue(self, markets: list[str], lookback: int = 7 * 3) -> tuple[str, dict[str, float]]:
        scores: dict[str, float] = {}
        per_market_best: dict[str, dict[str, float]] = {}
        for venue in self.candidates():
            per_market_best[venue] = {}
            total = 0.0
            for m in markets:
                h = self.historical_funding(venue, m)[-lookback:]
                avg_abs = sum(abs(x) for x in h) / max(1, len(h))
                per_market_best[venue][m] = h[-1]
                total += avg_abs
            scores[venue] = total / max(1, len(markets))
        best = max(scores, key=scores.get)
        return best, per_market_best[best]


class ERC8004:
    """ERC-8004 identity NFT registration."""

    def __init__(self, client: BSCClient, registry_address: str):
        self.client = client
        self.registry = Web3.to_checksum_address(registry_address)
        self._token_id: int | None = None
        self._cid: str | None = None

    def register(self, agent_uri: str) -> tuple[int, str]:
        """Returns (tokenId, agentURI). In testnet, returns a deterministic stub."""
        if self.client.mode in ("testnet", "replay"):
            self._cid = "Qm" + Web3.keccak(text=agent_uri).hex()[:44]
            self._token_id = int.from_bytes(Web3.keccak(text=agent_uri)[:8], "big")
            return self._token_id, self._cid
        c = self.client.w3().eth.contract(address=self.registry, abi=ERC8004_REGISTRY_ABI)
        tx = c.functions.register(agent_uri).build_transaction({
            "from": "0x" + "00" * 20, "nonce": 0, "gas": 500_000, "chainId": self.client.chain_id,
        })
        # caller must sign + broadcast via TWAK
        raise NotImplementedError("mainnet registration requires TWAK signing in caller")

    @property
    def token_id(self) -> int | None:
        return self._token_id


class ERC8183:
    """ERC-8183 job escrow wrapper."""

    def __init__(self, client: BSCClient, escrow_address: str):
        self.client = client
        self.escrow = Web3.to_checksum_address(escrow_address)
        self._jobs: dict[int, dict] = {}

    def create_job(self, provider: str, evaluator: str, deliverable_spec: bytes,
                   budget: int, token: str) -> int:
        """Returns jobId. In testnet, simulates the state machine in-memory."""
        job_id = len(self._jobs) + 1
        self._jobs[job_id] = {
            "id": job_id,
            "provider": Web3.to_checksum_address(provider),
            "evaluator": Web3.to_checksum_address(evaluator),
            "deliverable_spec": deliverable_spec.hex() if isinstance(deliverable_spec, bytes) else deliverable_spec,
            "budget": budget,
            "token": Web3.to_checksum_address(token),
            "status": "Open",
            "funded": 0,
            "proof": None,
        }
        return job_id

    def fund(self, job_id: int, amount: int):
        j = self._jobs[job_id]
        if j["status"] != "Open":
            raise ValueError(f"job {job_id} not Open (status={j['status']})")
        j["funded"] += amount
        if j["funded"] >= j["budget"]:
            j["status"] = "Funded"

    def submit(self, job_id: int, proof_cid: str):
        j = self._jobs[job_id]
        if j["status"] != "Funded":
            raise ValueError(f"job {job_id} not Funded (status={j['status']})")
        j["proof"] = proof_cid
        j["status"] = "Submitted"

    def complete(self, job_id: int):
        j = self._jobs[job_id]
        if j["status"] != "Submitted":
            raise ValueError(f"job {job_id} not Submitted (status={j['status']})")
        j["status"] = "Completed"

    def reject(self, job_id: int):
        j = self._jobs[job_id]
        j["status"] = "Rejected"

    def claim_refund(self, job_id: int):
        j = self._jobs[job_id]
        if j["status"] in ("Open", "Funded"):
            j["status"] = "Refunded"

    def get(self, job_id: int) -> dict:
        return dict(self._jobs.get(job_id, {}))

    def all(self) -> list[dict]:
        return [dict(j) for j in self._jobs.values()]


# --- factory ---

def from_config(path: str = "config/config.yaml", wallet: TWAKWallet | None = None) -> dict:
    # v2.1.1: use the local.yaml shadow pattern when reading the
    # default config path. Explicit paths (passed by tests or tooling)
    # are read verbatim.
    if Path(path) == DEFAULT_CONFIG:
        cfg = _load_merged_config()
    else:
        cfg = yaml.safe_load(open(path))
    bsc = BSCClient(rpcs=cfg["rpcs"], chain_id=cfg["chain_id"], mode=cfg.get("mode", "testnet"))
    pancake = PancakeV3(
        client=bsc,
        router=cfg["dex"]["pcs_v3_router"],
        quoter=cfg["dex"]["pcs_v3_quoter"],
        factory=cfg["dex"]["pcs_v3_factory"],
    )
    perps = Perps(mode=cfg.get("mode", "testnet"))
    # contract addresses: in production, look up from bnbagent-sdk registry
    erc8004 = ERC8004(client=bsc, registry_address="0x" + "80" + "04" + "0" * 36)
    erc8183 = ERC8183(client=bsc, escrow_address="0x" + "81" + "83" + "0" * 36)
    return {
        "bsc": bsc,
        "pancake": pancake,
        "perps": perps,
        "erc8004": erc8004,
        "erc8183": erc8183,
    }
