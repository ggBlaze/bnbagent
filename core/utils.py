"""Shared helpers used by the 3 sleeves."""
from __future__ import annotations

from decimal import Decimal

# Lazy import: web3 is heavy and optional — only needed for the
# on-chain decimals() fallback in token_decimals(). Importing at
# module load time breaks test environments that don't have web3
# installed (and would also slow CLI startup).
try:
    from web3 import Web3 as _Web3
except ImportError:  # pragma: no cover
    _Web3 = None


def token_address(cfg: dict, symbol: str) -> str:
    """Resolve a symbol → checksummed BSC-20 address from config.

    Supports both the nested-dict form (config.yaml as of v1.1) and the
    legacy flat string form.
    """
    tokens = cfg.get("tokens", {}) or {}
    for tok in tokens.values():
        if isinstance(tok, dict) and tok.get("symbol") == symbol:
            return tok["bsc_address"]
    entry = tokens.get(symbol)
    if isinstance(entry, dict):
        return entry.get("bsc_address", "0x" + "00" * 20)
    if isinstance(entry, str):
        return entry
    return "0x" + "00" * 20


def usdc_to_units(amount_usdc: Decimal | float, decimals: int = 6) -> int:
    """Convert a USDC amount to integer token units.

    v2.2.4 (decimals bugfix): the default of 6 is preserved for
    backwards compat (testnet USDC + historical mainnet USDC have 6
    decimals). For BSC mainnet USDC/USDT (the contracts the agent
    actually trades), the live `decimals()` call returns 18. Callers
    that know the right value should pass it explicitly via
    `token_decimals(symbol, cfg)` to avoid dust swaps.
    """
    return int(Decimal(str(amount_usdc)) * Decimal(10 ** decimals))


# v2.2.4: module-level cache for token decimals. The BSC mainnet USDC
# contract (`0x8AC76a51...`) reports `decimals() == 18` even though the
# agent historically treated it as 6. Same for USDT. The trading code
# was always using `10**6` which produced dust amounts (8e-14 USDC per
# $0.08 notional) and burned ~$30 of BNB on gas in one hour of
# spam-mining before I caught it. Reading from cfg first, then on-chain
# as fallback, so we never trust a hardcoded 6 again.
_TOKEN_DECIMALS_CACHE: dict[str, int] = {}


def token_decimals(symbol: str, cfg: dict | None = None,
                   w3: "Web3 | None" = None) -> int:
    """Return the on-chain decimals for `symbol`.

    Lookup order:
    1. Module-level cache (filled by an earlier call).
    2. cfg["tokens"][symbol]["decimals"] (the operator-set value in
       config/config.yaml or config/local.yaml).
    3. On-chain `decimals()` call against cfg["tokens"][symbol]["bsc_address"].
    4. Hardcoded fallback (WBNB=18, USDT/USDC=18, ETH=18, others=18).

    Never returns 6 for any mainnet USDC/USDT contract: those have
    18 decimals on BSC mainnet as of the 2026 chain state. The
    historical 6-decimal assumption was wrong and is documented in
    core/utils.py::usdc_to_units docstring.
    """
    sym = (symbol or "").upper()
    if sym in _TOKEN_DECIMALS_CACHE:
        return _TOKEN_DECIMALS_CACHE[sym]
    # 2. config
    if cfg:
        tokens = cfg.get("tokens") or {}
        entry = tokens.get(sym) or tokens.get(symbol) or {}
        if isinstance(entry, dict) and entry.get("decimals") is not None:
            d = int(entry["decimals"])
            _TOKEN_DECIMALS_CACHE[sym] = d
            return d
    # 3. on-chain
    if cfg and w3 is not None and _Web3 is not None:
        tokens = cfg.get("tokens") or {}
        entry = tokens.get(sym) or {}
        addr = entry.get("bsc_address") if isinstance(entry, dict) else None
        if addr:
            try:
                abi = [{"constant":True,"inputs":[],"name":"decimals",
                        "outputs":[{"name":"","type":"uint8"}],
                        "type":"function"}]
                c = w3.eth.contract(
                    address=_Web3.to_checksum_address(addr), abi=abi
                )
                d = int(c.functions.decimals().call())
                _TOKEN_DECIMALS_CACHE[sym] = d
                return d
            except Exception:
                pass
    # 4. fallback
    fallback = {"WBNB": 18, "USDC": 18, "USDT": 18, "ETH": 18, "CAKE": 18, "BTCB": 18}
    d = fallback.get(sym, 18)
    _TOKEN_DECIMALS_CACHE[sym] = d
    return d


def clear_token_decimals_cache() -> None:
    """Reset the module-level cache (mostly for tests)."""
    _TOKEN_DECIMALS_CACHE.clear()


def safe_div(numer: Decimal, denom: Decimal, default: Decimal = Decimal(0)) -> Decimal:
    """Division that returns `default` when denom is zero."""
    if denom == 0:
        return default
    return numer / denom


# v2.3.9: clamp a USDC swap amount to the wallet's actual on-chain
# USDC balance. Without this, every swap where the configured notional
# exceeds the live balance reverts on-chain with STF (Safe Transfer
# From) when the router calls transferFrom(user, pool, amount). The
# bot's internal state thinks the trade opened (because the broadcast
# was submitted), but the on-chain reality is a revert — a silent state
# divergence that burns gas on every attempt.
#
# Returns (amount_in_units, was_clamped, balance_units). was_clamped
# is True when the requested amount was reduced because of insufficient
# balance. Callers should log was_clamped and consider whether to skip
# the trade entirely (e.g. if requested == clamped + small change, the
# notional is too tiny to bother).
def clamp_to_usdc_balance(
    requested_units: int,
    balance_units: int,
    *,
    min_amount_units: int = 0,
) -> tuple[int, bool, int]:
    """Clamp requested_units to balance_units (the wallet's actual
    USDC balance in raw token units).

    Args:
        requested_units: the amount the strategy wants to swap, in
            raw token units (already scaled by 10**decimals).
        balance_units: the on-chain USDC balance in raw token units.
        min_amount_units: skip (return 0) if the clamped amount would
            be below this threshold. 0 means "always proceed".

    Returns:
        (clamped_amount, was_clamped, balance). When balance is below
        min_amount_units, clamped_amount is 0 and was_clamped is True.
    """
    if balance_units < min_amount_units:
        return 0, True, balance_units
    if requested_units <= balance_units:
        return requested_units, False, balance_units
    return balance_units, True, balance_units


# v2.3.9: clamp a USDC swap amount to the wallet's actual on-chain
# USDC balance. Without this, every swap where the configured notional
# exceeds the live balance reverts on-chain with STF (Safe Transfer
# From) when the router calls transferFrom(user, pool, amount). The
# bot's internal state thinks the trade opened (because the broadcast
# was submitted), but the on-chain reality is a revert — a silent state
# divergence that burns gas on every attempt.
#
# Returns (amount_in_units, was_clamped, balance_units). was_clamped
# is True when the requested amount was reduced because of insufficient
# balance. Callers should log was_clamped and consider whether to skip
# the trade entirely (e.g. if requested == clamped + small change, the
# notional is too tiny to bother).
def clamp_to_usdc_balance(
    requested_units: int,
    balance_units: int,
    *,
    min_amount_units: int = 0,
) -> tuple[int, bool, int]:
    """Clamp requested_units to balance_units (the wallet's actual
    USDC balance in raw token units).

    Args:
        requested_units: the amount the strategy wants to swap, in
            raw token units (already scaled by 10**decimals).
        balance_units: the on-chain USDC balance in raw token units.
        min_amount_units: skip (return 0) if the clamped amount would
            be below this threshold. 0 means "always proceed".

    Returns:
        (clamped_amount, was_clamped, balance). When balance is below
        min_amount_units, clamped_amount is 0 and was_clamped is True.
    """
    if balance_units < min_amount_units:
        return 0, True, balance_units
    if requested_units <= balance_units:
        return requested_units, False, balance_units
    return balance_units, True, balance_units
