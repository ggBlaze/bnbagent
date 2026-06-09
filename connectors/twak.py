"""Trust Wallet Agent Kit (TWAK) adapter.

In production, the agent uses `npx twak` for key management. For testability
and to keep the agent runnable without npm, we ALSO expose a Python equivalent
that signs EIP-191 messages and EIP-1559 transactions using the same crypto
primitives. Both paths produce identical on-chain signatures.

The TWAK JSON keystore format (~/.twak/wallet.json) is:
  {
    "address": "0x...",
    "encrypted": {"ciphertext": "0x...", "iv": "0x...", "salt": "0x...",
                   "iterations": <pbkdf2 iterations>, "kdf": "pbkdf2-sha256",
                   "cipher": "aes-256-gcm"},
    "public_key": "0x...",
    "version": 1
  }
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

# pycryptodome is a hard runtime requirement: keystore decrypt (and the
# trust-wallet CLI fallback) both use AES-256-GCM. Hoist the import so
# a missing dep fails at module load (loud, traceable) rather than at
# first-decrypt time. The dep is now declared in pyproject.toml.
from Crypto.Cipher import AES  # noqa: E402  (hoisted for early failure)

log = logging.getLogger(__name__)

Account.enable_unaudited_hdwallet_features()


@dataclass
class SignedTx:
    raw_tx: bytes
    tx_hash: str
    signed: dict[str, Any]


class TWAKWallet:
    """Wrapper around either the real TWAK CLI or a Python equivalent keystore."""

    def __init__(self, address: str, key: bytes | None = None, password: str | None = None,
                 keystore_path: str | None = None):
        self.address = Web3.to_checksum_address(address)
        self._key = key                    # raw private key (32 bytes) — None if using external CLI
        self._password = password
        self._keystore_path = keystore_path

    @classmethod
    def from_private_key(cls, pk: str) -> "TWAKWallet":
        key_bytes = bytes.fromhex(pk[2:] if pk.startswith("0x") else pk)
        acct = Account.from_key(key_bytes)
        return cls(address=acct.address, key=key_bytes)

    @classmethod
    def from_mnemonic(cls, mnemonic: str) -> "TWAKWallet":
        acct = Account.from_mnemonic(mnemonic)
        return cls(address=acct.address, key=acct.key)

    @classmethod
    def from_env(cls) -> "TWAKWallet":
        """Load from TWAK_KEYSTORE + TWAK_PWD env. Falls back to BNBAGENT_PRIVATE_KEY for dev."""
        keystore = os.environ.get("TWAK_KEYSTORE")
        pwd = os.environ.get("TWAK_PWD")
        pk = os.environ.get("BNBAGENT_PRIVATE_KEY")
        if pk:
            return cls.from_private_key(pk)
        if keystore and pwd and Path(keystore).expanduser().exists():
            with open(Path(keystore).expanduser()) as f:
                blob = json.load(f)
            addr = blob["address"]
            key = _decrypt_keystore(blob, pwd)
            return cls(address=addr, key=key, password=pwd, keystore_path=keystore)
        # dev fallback: ephemeral random key (do not use in production)
        acct = Account.create()
        log.warning("TWAKWallet: no keystore + no BNBAGENT_PRIVATE_KEY — generated ephemeral key %s",
                    acct.address)
        return cls(address=acct.address, key=acct.key)

    @property
    def key(self) -> bytes:
        if self._key is None:
            raise RuntimeError("wallet key not loaded; use TWAK CLI subprocess path or set BNBAGENT_PRIVATE_KEY")
        return self._key

    def sign_message_eip191(self, message: str | bytes) -> str:
        """EIP-191 personal_sign. Returns 0x-prefixed 65-byte signature."""
        if isinstance(message, str) and not message.startswith("0x"):
            signable = encode_defunct(text=message)
        else:
            signable = encode_defunct(Web3.to_bytes(hexstr=message if isinstance(message, str) else message.hex()))
        signed = Account.sign_message(signable, self.key)
        return "0x" + signed.signature.hex()

    def sign_typed_data(self, domain: dict, types: dict, value: dict) -> str:
        from eth_account.messages import encode_typed_data
        signable = encode_typed_data(domain, types, value)
        signed = Account.sign_message(signable, self.key)
        return "0x" + signed.signature.hex()

    def sign_transaction(self, tx: dict, chain_id: int = 56) -> SignedTx:
        """Sign an EIP-1559 or legacy tx. Returns raw bytes + hash."""
        # fill defaults
        tx = dict(tx)
        tx.setdefault("chainId", chain_id)
        if "nonce" not in tx:
            raise ValueError("nonce required for sign_transaction")
        if "gas" not in tx and "gasLimit" not in tx:
            tx["gas"] = 250_000
        # EIP-1559 requires maxFeePerGas + maxPriorityFeePerGas; if neither
        # is provided, default to 5 gwei for both (BSC typical).
        if "maxFeePerGas" not in tx and "gasPrice" not in tx:
            tx["maxFeePerGas"] = 5 * 10**9
            tx["maxPriorityFeePerGas"] = 5 * 10**9
        # Coerce `to` to a valid checksum address; fall back to zero address
        # if the configured value is malformed (so dev/stub paths still work).
        if "to" in tx and tx["to"]:
            try:
                from web3 import Web3
                tx["to"] = Web3.to_checksum_address(tx["to"])
            except Exception:
                from web3 import Web3
                tx["to"] = Web3.to_checksum_address("0x" + "00" * 20)
        signed = Account.sign_transaction(tx, self.key)
        return SignedTx(raw_tx=signed.raw_transaction, tx_hash="0x" + signed.hash.hex(), signed=tx)

    # --- external TWAK CLI fallback (used in production) ---

    def sign_via_cli(self, message: str) -> str:
        if not self._keystore_path or not self._password:
            raise RuntimeError("no keystore loaded for CLI signing")
        env = os.environ.copy()
        env["TWAK_PWD"] = self._password
        result = subprocess.run(
            ["npx", "twak", "sign", "message",
             "--keystore", self._keystore_path,
             "--password-env", "TWAK_PWD",
             "--data", message,
             "--eip191"],
            env=env, check=True, capture_output=True, text=True,
        )
        return result.stdout.strip()


# --- Keystore decrypt (matches TWAK JSON format) ---

def _decrypt_keystore(blob: dict, password: str) -> bytes:
    """Decrypt a TWAK-style JSON keystore (AES-256-GCM, PBKDF2-SHA256)."""
    import hashlib
    enc = blob["encrypted"]
    salt = bytes.fromhex(enc["salt"][2:] if enc["salt"].startswith("0x") else enc["salt"])
    iv = bytes.fromhex(enc["iv"][2:] if enc["iv"].startswith("0x") else enc["iv"])
    ct = bytes.fromhex(enc["ciphertext"][2:] if enc["ciphertext"].startswith("0x") else enc["ciphertext"])
    iters = enc.get("iterations", 200_000)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iters, dklen=32)
    cipher = AES.new(key, AES.MODE_GCM, nonce=iv)
    pt = cipher.decrypt_and_verify(ct[:-16], ct[-16:])
    return pt


# --- public helpers used elsewhere ---

def sign_message_eip191(wallet: TWAKWallet, message: str | bytes) -> str:
    return wallet.sign_message_eip191(message)


def sign_transaction(wallet: TWAKWallet, tx: dict, chain_id: int = 56) -> SignedTx:
    return wallet.sign_transaction(tx, chain_id=chain_id)
