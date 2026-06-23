"""core/balances.py — read live on-chain balances for the operator wallet.

Backs the dashboard's "Wallet Holdings" panel (right rail). Tries each
RPC in the operator's setup list in order, calls `eth_get_balance` for
the native asset and `balanceOf` on a small allowlist of well-known
ERC-20 / BEP-20 tokens, and returns the raw on-chain numbers.

Stables are annotated "≈ $X" (1:1 USD) since we don't have a price
oracle here. BNB / ETH are returned as raw numbers. The dashboard
refreshes on a 30s cadence because every read is an RPC call.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

log = logging.getLogger(__name__)


# Minimal ERC-20 / BEP-20 ABI fragment for balanceOf
_ERC20_BALANCE_OF_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    }
]

# Token allowlist: (symbol, mainnet_addr, testnet_addr, decimals)
# Testnet addresses are best-effort; on BSC testnet, the only realistic
# USDC/USDT contracts are the official faucets. The endpoint picks
# the right one based on the chain id of the connected Web3.
BSC_TOKENS: list[tuple[str, str, str, int]] = [
    ("USDT", "0x55d398326f99059fF775485246999027B3197955", "0x337610d27c682E347C9c60F4F926C341e33a4e99", 18),
    ("USDC", "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", "0x64544969ed7EBf2f0B664F8c1F8f2F8B4b1F8E8E", 18),
    ("BUSD", "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56", "", 18),
    # v2.3.5c: include the BSC-pegged ETH (BEP-20 0x2170...) so the
    # wallet-total panel shows the value of funding payouts the bot
    # received in ETH. Decimals match ETH on Ethereum (18). Not a
    # stable — priced in USD via the oracle like BNB.
    ("ETH",  "0x2170Ed0880ac9A755fd29B2688956BD959F933F8", "", 18),
]

BASE_TOKENS: list[tuple[str, str, str, int]] = [
    # USDC is the only token the x402 flow actually needs on Base.
    ("USDC", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "0x036CbD53842c5426634e7929541eC2318f3dCF7e", 6),
]

STABLE_SYMBOLS = {"USDT", "USDC", "BUSD", "DAI", "USDP", "TUSD"}


@dataclass
class TokenBalance:
    symbol: str
    address: str
    balance: str       # human-readable (already divided by 10^decimals)
    raw: int
    decimals: int
    usd: Optional[float] = None  # populated only for stables (1:1)


@dataclass
class ChainBalances:
    native: Optional[TokenBalance] = None
    tokens: list[TokenBalance] = field(default_factory=list)
    error: str = ""


@dataclass
class WalletBalances:
    wallet: str = ""
    chain_id: int = 0
    bsc: ChainBalances = field(default_factory=ChainBalances)
    base: Optional[ChainBalances] = None  # populated only if x402 is active
    base_active: bool = False
    fetched_at: int = 0
    error: str = ""


def _wei_to_human(raw: Optional[int], decimals: int) -> str:
    if raw is None:
        return "0"
    if decimals == 0:
        return str(raw)
    human = raw / (10 ** decimals)
    s = f"{human:.6f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _connect_first(rpcs: list[str], timeout: float = 5.0):
    """Try each RPC in order; return the first connected Web3, or None.

    web3.py 7.x renamed geth_poa_middleware → ExtraDataToPOAMiddleware
    and moved it to web3.middleware.proof_of_authority. The pre-fix
    code did `from web3.middleware import geth_poa_middleware` inside
    a try/except that returned None on ImportError — making EVERY
    RPC look unreachable on web3.py 7.x, even when curl works. The
    fix tries the new path first, falls back to 6.x, and only
    returns None if Web3 itself can't be imported.
    """
    from web3 import Web3
    # web3.py 7.x path first, fall back to 6.x. Both imports may
    # fail on a stripped web3 install; treat middleware as optional
    # rather than failing the whole RPC connect.
    _POA = None
    try:
        from web3.middleware.proof_of_authority import ExtraDataToPOAMiddleware as _POA  # type: ignore
    except ImportError:
        try:
            from web3.middleware import geth_poa_middleware as _POA  # type: ignore
        except ImportError:
            log.debug("no POA middleware available on this web3 version")
    for url in rpcs:
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": timeout}))
            if _POA is not None:
                w3.middleware_onion.inject(_POA, layer=0)
            if w3.is_connected():
                return w3
        except Exception as e:
            log.debug("rpc %s failed: %s", url, e)
            continue
    return None


def _pick_token_addr(token: tuple, chain_id: int) -> str:
    """Return mainnet address for chain 56 / 8453, testnet for 97 / 84532, '' otherwise."""
    sym, main_addr, test_addr, _dec = token
    if chain_id in (56, 8453):
        return main_addr
    if chain_id in (97, 84532):
        return test_addr or ""
    return ""


def _fetch_chain(w3, address: str, tokens: list, native_symbol: str, native_decimals: int) -> ChainBalances:
    from web3 import Web3
    out = ChainBalances()
    addr = Web3.to_checksum_address(address)
    chain_id = w3.eth.chain_id

    # Native
    try:
        raw = w3.eth.get_balance(addr)
        out.native = TokenBalance(
            symbol=native_symbol,
            address="",
            balance=_wei_to_human(raw, native_decimals),
            raw=raw,
            decimals=native_decimals,
        )
    except Exception as e:
        out.error = f"native: {e}"

    # Tokens
    for token in tokens:
        taddr = _pick_token_addr(token, chain_id)
        if not taddr:
            continue
        sym = token[0]
        dec = token[3]
        try:
            contract = w3.eth.contract(
                address=Web3.to_checksum_address(taddr),
                abi=_ERC20_BALANCE_OF_ABI,
            )
            raw = contract.functions.balanceOf(addr).call()
            human = _wei_to_human(raw, dec)
            usd = float(human) if sym in STABLE_SYMBOLS else None
            out.tokens.append(TokenBalance(
                symbol=sym,
                address=taddr,
                balance=human,
                raw=raw,
                decimals=dec,
                usd=usd,
            ))
        except Exception as e:
            log.debug("token %s read failed on chain %s: %s", sym, chain_id, e)
            continue
    return out


def get_wallet_balances(
    wallet_address: str,
    bsc_rpcs: list[str],
    chain_id: int,
    base_active: bool = False,
    base_rpcs: Optional[list[str]] = None,
) -> WalletBalances:
    """Read live balances for the operator wallet.

    Returns a WalletBalances with bsc populated always (if the wallet +
    rpcs are configured) and base populated only when base_active is True.
    All fields are best-effort: errors are captured per-chain and the
    endpoint never raises.
    """
    out = WalletBalances(
        wallet=wallet_address,
        chain_id=chain_id,
        base_active=base_active,
        fetched_at=int(time.time()),
    )
    if not wallet_address:
        out.error = "no wallet configured"
        return out
    if not bsc_rpcs:
        out.error = "no BSC RPCs configured"
        return out

    # BSC
    w3 = _connect_first(bsc_rpcs)
    if w3 is None:
        out.bsc.error = "no BSC RPC reachable"
    else:
        try:
            out.bsc = _fetch_chain(w3, wallet_address, BSC_TOKENS, "BNB", 18)
        except Exception as e:
            out.bsc.error = f"bsc read failed: {e}"

    # Base (only if x402 active)
    if base_active:
        if not base_rpcs:
            out.base = ChainBalances(error="no Base RPCs configured")
        else:
            w3b = _connect_first(base_rpcs)
            if w3b is None:
                out.base = ChainBalances(error="no Base RPC reachable")
            else:
                try:
                    out.base = _fetch_chain(w3b, wallet_address, BASE_TOKENS, "ETH", 18)
                except Exception as e:
                    out.base = ChainBalances(error=f"base read failed: {e}")

    return out


def balances_to_dict(b: WalletBalances) -> dict:
    """Convert the dataclass tree to a JSON-safe dict for the API."""
    def _tb(t: Optional[TokenBalance]):
        return asdict(t) if t is not None else None

    def _cb(c: Optional[ChainBalances]):
        if c is None:
            return None
        return {
            "native": _tb(c.native),
            "tokens": [_tb(t) for t in (c.tokens or [])],
            "error": c.error,
        }

    return {
        "wallet": b.wallet,
        "chain_id": b.chain_id,
        "bsc": _cb(b.bsc),
        "base": _cb(b.base),
        "base_active": b.base_active,
        "fetched_at": b.fetched_at,
        "error": b.error,
    }
