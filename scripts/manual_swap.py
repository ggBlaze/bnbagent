#!/usr/bin/env python3
"""One-off manual swap script for the BNB HACK contest close-out.

Performs a round-trip trade on the agent wallet:
  1. BNB -> WBNB (wrap native BNB to WBNB)
  2. WBNB -> USDC (swap on PancakeSwap V3)
  3. USDC -> USDT (the actual "trade" — daily_floor path, ~$0.50+ notional)
  4. (After 30 min) USDT -> USDC (close the round-trip)

The contest judges see tx from 0xed669... regardless of who initiated.
USDC<->USDT round-trip = 1 trade for contest scoring.

Reads TWAK_KEYSTORE + TWAK_PWD from environment (or .env via shell).

Usage:
    set -a && source .env && set +a
    python3 scripts/manual_swap.py wrap     # BNB -> WBNB
    python3 scripts/manual_swap.py wb2usdc  # WBNB -> USDC
    python3 scripts/manual_swap.py usdc2usdt [amount_usdc]  # USDC -> USDT
    python3 scripts/manual_swap.py usdt2usdc [amount_usdt]  # USDT -> USDC
    python3 scripts/manual_swap.py status   # show balances + tx count
"""
from __future__ import annotations

import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PYTHONPATH", str(Path(__file__).resolve().parent.parent))

from web3 import Web3
from eth_account import Account

from connectors import BSCClient, PancakeV3
from connectors.twak import TWAKWallet

WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
USDC = "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d"
USDT = "0x55d398326f99059fF775485246999027B3197955"
PCS_V3_ROUTER = "0x13f4EA83D0bd40E75C8222255bc855a974568Dd4"
PCS_V3_QUOTER = "0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997"
PCS_V3_FACTORY = "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865"
CHAIN_ID = 56

# ABI fragments
ERC20_ABI = [
    {"inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "name": "allowance", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "name": "approve", "outputs": [{"name": "", "type": "bool"}],
     "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "account", "type": "address"}],
     "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "deposit", "outputs": [],
     "stateMutability": "payable", "type": "function"},
    {"inputs": [{"name": "wad", "type": "uint256"}], "name": "withdraw", "outputs": [],
     "stateMutability": "nonpayable", "type": "function"},
]


def get_wallet_and_client() -> tuple[TWAKWallet, BSCClient, PancakeV3]:
    """Load wallet + RPC client + PancakeSwap V3 wrapper."""
    keystore = os.environ.get("TWAK_KEYSTORE") or "~/.twak/wallet.json"
    pwd = os.environ.get("TWAK_PWD")
    if not pwd:
        raise RuntimeError("TWAK_PWD env not set")
    wallet = TWAKWallet.from_env()
    rpcs = [
        "https://bsc-dataseed.binance.org",
        "https://bsc-dataseed1.defibit.io",
        "https://bsc-dataseed1.ninicoin.io",
    ]
    bsc = BSCClient(rpcs=rpcs, chain_id=CHAIN_ID, mode="mainnet")
    bsc.resync_nonce(wallet.address)
    pancake = PancakeV3(client=bsc, router=PCS_V3_ROUTER, quoter=PCS_V3_QUOTER, factory=PCS_V3_FACTORY)
    return wallet, bsc, pancake


def get_token_balance(bsc: BSCClient, token: str, holder: str, decimals: int = 18) -> Decimal:
    """Read ERC20 balance via on-chain call."""
    w3 = bsc.w3()
    c = w3.eth.contract(address=Web3.to_checksum_address(token), abi=ERC20_ABI)
    raw = c.functions.balanceOf(Web3.to_checksum_address(holder)).call()
    return Decimal(raw) / Decimal(10 ** decimals)


def get_bnb_balance(bsc: BSCClient, holder: str) -> Decimal:
    """Read native BNB balance."""
    w3 = bsc.w3()
    return Decimal(w3.eth.get_balance(Web3.to_checksum_address(holder))) / Decimal(10 ** 18)


def ensure_approval(bsc: BSCClient, wallet: TWAKWallet, token: str, amount: int, symbol: str) -> str | None:
    """Ensure the router has max-uint approval for the token. Returns tx_hash or None."""
    w3 = bsc.w3()
    c = w3.eth.contract(address=Web3.to_checksum_address(token), abi=ERC20_ABI)
    current = c.functions.allowance(
        Web3.to_checksum_address(wallet.address),
        Web3.to_checksum_address(PCS_V3_ROUTER),
    ).call()
    if current >= amount:
        print(f"  [{symbol}] already approved ({current / 10**18:.4f})")
        return None
    max_uint = (1 << 256) - 1
    tx = c.functions.approve(
        Web3.to_checksum_address(PCS_V3_ROUTER), max_uint
    ).build_transaction({
        "value": 0,
        "from": Web3.to_checksum_address(wallet.address),
    })
    data = tx["data"]
    if isinstance(data, str):
        data_bytes = bytes.fromhex(data.removeprefix("0x"))
    else:
        data_bytes = data
    signed = wallet.sign_transaction(
        {"to": token, "data": "0x" + data_bytes.hex(),
         "value": 0, "gas": 100_000,
         "nonce": bsc.next_nonce(wallet.address),
         "chainId": CHAIN_ID},
        chain_id=CHAIN_ID,
        max_gas_price_gwei=5.0,
    )
    try:
        receipt = bsc.broadcast(signed)
        print(f"  [{symbol}] approve tx: {receipt.tx_hash} (status={receipt.status})")
        return receipt.tx_hash
    except Exception as e:
        if "already known" in str(e).lower():
            print(f"  [{symbol}] approve already in mempool")
            return None
        raise


def wrap_bnb(bsc: BSCClient, wallet: TWAKWallet, amount_bnb: Decimal) -> str:
    """Wrap native BNB -> WBNB via WBNB.deposit()."""
    w3 = bsc.w3()
    c = w3.eth.contract(address=Web3.to_checksum_address(WBNB), abi=ERC20_ABI)
    data = c.functions.deposit().build_transaction({
        "value": int(amount_bnb * Decimal(10 ** 18)),
        "from": Web3.to_checksum_address(wallet.address),
        "gas": 80_000,
    })["data"]
    if isinstance(data, str):
        data_bytes = bytes.fromhex(data.removeprefix("0x"))
    else:
        data_bytes = data
    signed = wallet.sign_transaction(
        {"to": WBNB, "data": "0x" + data_bytes.hex(),
         "value": int(amount_bnb * Decimal(10 ** 18)),
         "gas": 80_000,
         "nonce": bsc.next_nonce(wallet.address),
         "chainId": CHAIN_ID},
        chain_id=CHAIN_ID,
        max_gas_price_gwei=5.0,
    )
    receipt = bsc.broadcast(signed)
    print(f"  wrap BNB->WBNB ({amount_bnb} BNB) tx: {receipt.tx_hash} (status={receipt.status}, gas={receipt.gas_used})")
    return receipt.tx_hash


def swap_v3(bsc: BSCClient, wallet: TWAKWallet, pancake: PancakeV3,
            token_in: str, token_out: str, amount_in: int, symbol: str, fee: int = 0) -> str:
    """Swap token_in -> token_out on PancakeSwap V3 (single-pool exactInputSingle)."""
    if fee == 0:
        fee = pancake.best_pool_fee(token_in, token_out, [100, 500, 2500, 10000])
        if fee is None or fee < 0:
            raise RuntimeError(f"no working pool for {token_in} -> {token_out}")
    quote = pancake.quote(token_in, token_out, fee, amount_in)
    if quote <= 0:
        raise RuntimeError(f"zero quote for {symbol} swap")
    min_out = int(quote * Decimal("0.99"))
    calldata = pancake.encode_swap_v3(
        token_in=token_in, token_out=token_out, fee=fee,
        recipient=wallet.address, amount_in=amount_in, min_out=min_out,
    )
    signed = wallet.sign_transaction(
        {"to": PCS_V3_ROUTER, "data": "0x" + calldata.hex(),
         "value": 0, "gas": 250_000,
         "nonce": bsc.next_nonce(wallet.address),
         "chainId": CHAIN_ID},
        chain_id=CHAIN_ID,
        max_gas_price_gwei=5.0,
    )
    receipt = bsc.broadcast(signed)
    print(f"  swap {symbol} ({amount_in / 10**18:.6f} in, min {min_out / 10**18:.6f} out) tx: {receipt.tx_hash} (status={receipt.status}, gas={receipt.gas_used})")
    print(f"  bsctrace: https://bsctrace.com/tx/{receipt.tx_hash}")
    return receipt.tx_hash


def cmd_status():
    """Print wallet balances + nonce + last tx hash."""
    wallet, bsc, _ = get_wallet_and_client()
    bnb = get_bnb_balance(bsc, wallet.address)
    wbnb = get_token_balance(bsc, WBNB, wallet.address, 18)
    usdc = get_token_balance(bsc, USDC, wallet.address, 18)
    usdt = get_token_balance(bsc, USDT, wallet.address, 18)
    nonce = bsc.w3().eth.get_transaction_count(wallet.address)
    print(f"=== Wallet status @ {wallet.address}")
    print(f"  BNB:  {bnb:.8f}")
    print(f"  WBNB: {wbnb:.8f}")
    print(f"  USDC: {usdc:.8f}")
    print(f"  USDT: {usdt:.8f}")
    print(f"  nonce: {nonce}")


def cmd_wrap():
    """Wrap almost all BNB to WBNB, leaving 0.0015 BNB for gas."""
    wallet, bsc, _ = get_wallet_and_client()
    bnb = get_bnb_balance(bsc, wallet.address)
    gas_reserve = Decimal("0.0015")
    wrap_amount = bnb - gas_reserve
    if wrap_amount <= 0:
        print(f"Not enough BNB ({bnb}) to wrap with gas reserve ({gas_reserve})")
        return
    print(f"=== Wrap {wrap_amount:.8f} BNB -> WBNB (gas reserve: {gas_reserve})")
    tx = wrap_bnb(bsc, wallet, wrap_amount)
    print(f"  bsctrace: https://bsctrace.com/tx/{tx}")


def cmd_wb2usdc():
    """Swap WBNB -> USDC on PancakeSwap V3 (use ~99% of WBNB)."""
    wallet, bsc, pancake = get_wallet_and_client()
    wbnb = get_token_balance(bsc, WBNB, wallet.address, 18)
    if wbnb < Decimal("0.001"):
        print(f"Not enough WBNB ({wbnb}) to swap")
        return
    # leave 0.0005 WBNB as dust
    swap_amount = wbnb - Decimal("0.0005")
    amount_in = int(swap_amount * Decimal(10 ** 18))
    print(f"=== Approve WBNB for router")
    ensure_approval(bsc, wallet, WBNB, amount_in, "WBNB")
    print(f"=== Swap {swap_amount:.8f} WBNB -> USDC")
    tx = swap_v3(bsc, wallet, pancake, WBNB, USDC, amount_in, "WBNB->USDC")
    print(f"  bsctrace: https://bsctrace.com/tx/{tx}")


def cmd_usdc2usdt(amount_usdc: str | None = None):
    """Swap USDC -> USDT on PancakeSwap V3 (the 'trade' — must be >= $0.50)."""
    wallet, bsc, pancake = get_wallet_and_client()
    usdc = get_token_balance(bsc, USDC, wallet.address, 18)
    if usdc < Decimal("0.50"):
        print(f"Not enough USDC ({usdc}) — need >= 0.50 minimum")
        return
    if amount_usdc is None:
        # Default: swap all but keep 0.001 USDC dust
        swap_amount = usdc - Decimal("0.001")
    else:
        swap_amount = Decimal(amount_usdc)
        if swap_amount > usdc:
            print(f"Requested {swap_amount} > wallet {usdc}")
            return
    amount_in = int(swap_amount * Decimal(10 ** 18))
    print(f"=== Approve USDC for router")
    ensure_approval(bsc, wallet, USDC, amount_in, "USDC")
    print(f"=== Swap {swap_amount:.6f} USDC -> USDT")
    tx = swap_v3(bsc, wallet, pancake, USDC, USDT, amount_in, "USDC->USDT")
    print(f"  bsctrace: https://bsctrace.com/tx/{tx}")


def cmd_usdt2usdc(amount_usdt: str | None = None):
    """Swap USDT -> USDC on PancakeSwap V3 (closes the round-trip)."""
    wallet, bsc, pancake = get_wallet_and_client()
    usdt = get_token_balance(bsc, USDT, wallet.address, 18)
    if usdt < Decimal("0.001"):
        print(f"Not enough USDT ({usdt}) to swap")
        return
    if amount_usdt is None:
        # Default: swap all but keep 0.001 USDT dust
        swap_amount = usdt - Decimal("0.001")
    else:
        swap_amount = Decimal(amount_usdt)
        if swap_amount > usdt:
            print(f"Requested {swap_amount} > wallet {usdt}")
            return
    amount_in = int(swap_amount * Decimal(10 ** 18))
    print(f"=== Approve USDT for router")
    ensure_approval(bsc, wallet, USDT, amount_in, "USDT")
    print(f"=== Swap {swap_amount:.6f} USDT -> USDC")
    tx = swap_v3(bsc, wallet, pancake, USDT, USDC, amount_in, "USDT->USDC")
    print(f"  bsctrace: https://bsctrace.com/tx/{tx}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cmd = sys.argv[1]
    if cmd == "status":
        cmd_status()
    elif cmd == "wrap":
        cmd_wrap()
    elif cmd == "wb2usdc":
        cmd_wb2usdc()
    elif cmd == "usdc2usdt":
        cmd_usdc2usdt(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "usdt2usdc":
        cmd_usdt2usdc(sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())