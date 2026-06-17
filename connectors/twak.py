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


class GasPriceTooHigh(Exception):
    """Raised by TWAKWallet.sign_transaction when the resulting fee would
    exceed a caller-supplied cap (v2.0.8-H4)."""

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
        """Load from TWAK_KEYSTORE + TWAK_PWD env. The BNBAGENT_PRIVATE_KEY
        plaintext-key path is opt-in: only when BNBAGENT_ALLOW_PK_ENV=1 is
        set, AND a CRITICAL log line is emitted. The dev fallback (no
        keystore, no PK env) generates an ephemeral random key for
        short-lived test runs."""
        keystore = os.environ.get("TWAK_KEYSTORE")
        pwd = os.environ.get("TWAK_PWD")
        pk = os.environ.get("BNBAGENT_PRIVATE_KEY")
        if pk:
            if os.environ.get("BNBAGENT_ALLOW_PK_ENV") != "1":
                # SECURITY: refuse to bypass the keystore silently. The
                # keystore exists for a reason — encryption at rest with
                # the operator's password. Loading a plaintext PK from an
                # env var means the raw key sits in process memory and
                # may have leaked into shell history, docker-compose, or
                # a systemd unit file. The operator must explicitly opt
                # in with BNBAGENT_ALLOW_PK_ENV=1 for this dev path.
                log.critical(
                    "BNBAGENT_PRIVATE_KEY is set but BNBAGENT_ALLOW_PK_ENV != 1. "
                    "Refusing to bypass the keystore. Set BNBAGENT_ALLOW_PK_ENV=1 "
                    "if you really mean it (NOT recommended for production)."
                )
                raise RuntimeError(
                    "BNBAGENT_PRIVATE_KEY is set but not opted in via "
                    "BNBAGENT_ALLOW_PK_ENV=1. Refusing to load a plaintext key."
                )
            log.critical(
                "SECURITY: BNBAGENT_PRIVATE_KEY is in use (BNBAGENT_ALLOW_PK_ENV=1). "
                "The keystore is being bypassed. Do NOT use this on mainnet."
            )
            return cls.from_private_key(pk)
        if keystore and pwd and Path(keystore).expanduser().exists():
            with open(Path(keystore).expanduser()) as f:
                blob = json.load(f)
            addr = blob["address"]
            key = _decrypt_keystore(blob, pwd)
            return cls(address=addr, key=key, password=pwd, keystore_path=keystore)
        # v2.1.8 (#7): if the operator declared a keystore but the file
        # is missing OR the password isn't set, FAIL LOUDLY. Silently
        # falling through to the ephemeral path means the agent runs
        # with a wallet the operator never authorized — observed live
        # when the agent booted before the wizard's wallet-import step
        # had written ~/.twak/wallet.json. The "two different wallets"
        # symptom in the dashboard was exactly this fallthrough.
        if keystore:
            ks_path = Path(keystore).expanduser()
            if not ks_path.exists():
                raise RuntimeError(
                    f"TWAK_KEYSTORE={keystore!r} but the file does not exist "
                    f"at {ks_path}. Import a wallet via the dashboard wizard "
                    f"(or copy an existing wallet.json), or unset TWAK_KEYSTORE "
                    f"to fall back to an ephemeral dev wallet."
                )
            if not pwd:
                raise RuntimeError(
                    f"TWAK_KEYSTORE is set ({keystore!r}) but TWAK_PWD is empty. "
                    f"Set TWAK_PWD to the keystore's password, or unset both "
                    f"to fall back to an ephemeral dev wallet."
                )
        # dev fallback: ephemeral random key (do not use in production).
        # Only reachable when NEITHER TWAK_KEYSTORE nor BNBAGENT_PRIVATE_KEY
        # is declared — the test/replay/fresh-install path.
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

    def sign_transaction(self, tx: dict, chain_id: int = 56,
                         max_gas_price_gwei: float | None = None) -> SignedTx:
        """Sign an EIP-1559 or legacy tx. Returns raw bytes + hash.

        `max_gas_price_gwei` is an OPTIONAL cap (in gwei) on the gas price
        we will sign at. If provided, AND if either:
          (a) the caller did not set a `gasPrice` / `maxFeePerGas` (and we'd
              default to 5 gwei), or
          (b) the caller's gas price is below the cap, we leave the price
              as-is or default it.
          If the caller's gas price WOULD exceed the cap, we raise
          GasPriceTooHigh before signing so a stuck-tx in a high-gas
          window doesn't burn the trade signal.

        This is the v2.0.8-H4 fix. In testnet the cap is ignored
        (mode=testnet, max_gas_price_gwei=None falls through). On
        mainnet, the sleeves read max_gas_price_gwei from the
        user-signed policy and pass it here.
        """
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
        # H-4 cap: refuse to sign if the resulting fee exceeds the policy cap.
        if max_gas_price_gwei is not None:
            cap_wei = int(max_gas_price_gwei * 10**9)
            actual = tx.get("gasPrice") or tx.get("maxFeePerGas") or 0
            if actual > cap_wei:
                raise GasPriceTooHigh(
                    f"gas price {actual / 1e9:.2f} gwei exceeds policy cap "
                    f"{max_gas_price_gwei:.2f} gwei — refusing to sign"
                )
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
