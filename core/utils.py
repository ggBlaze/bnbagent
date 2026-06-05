"""Shared helpers used by the 3 sleeves."""
from __future__ import annotations

from decimal import Decimal


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
    """Convert a USDC amount to integer token units (USDC is 6 decimals on BSC)."""
    return int(Decimal(str(amount_usdc)) * Decimal(10 ** decimals))


def safe_div(numer: Decimal, denom: Decimal, default: Decimal = Decimal(0)) -> Decimal:
    """Division that returns `default` when denom is zero."""
    if denom == 0:
        return default
    return numer / denom
