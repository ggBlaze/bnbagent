"""BNB Agent — connectors layer.

Each connector wraps a single external system:
  - cmc.py        → CoinMarketCap Data API + Data MCP (x402-paid)
  - x402.py       → EIP-3009 USDC micropayment flow against CMC
  - twak.py       → Trust Wallet Agent SDK (self-custody local signing)
  - bnb_sdk.py    → bnbagent-sdk (BSC, PancakeSwap v3, perps, ERC-8004, ERC-8183)
  - ipfs.py       → local IPFS pin client
"""

from .cmc import CMCClient
from .x402 import x402_pay, X402Required, decode_payment_requirements
from .twak import TWAKWallet, sign_message_eip191, sign_transaction
from .bnb_sdk import BSCClient, PancakeV3, Perps, ERC8004, ERC8183
from .ipfs import IPFSClient

__all__ = [
    "CMCClient", "x402_pay", "X402Required", "decode_payment_requirements",
    "TWAKWallet", "sign_message_eip191", "sign_transaction",
    "BSCClient", "PancakeV3", "Perps", "ERC8004", "ERC8183",
    "IPFSClient",
]
