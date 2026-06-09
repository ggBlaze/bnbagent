"""TWAK-compatible keystore: create / import / decrypt.

Produces a JSON keystore with the same shape as the Trust Wallet Agent Kit:
  {
    "address": "0x...",
    "encrypted": {"ciphertext": "0x...", "iv": "0x...", "salt": "0x...",
                   "iterations": <pbkdf2 iterations>, "kdf": "pbkdf2-sha256",
                   "cipher": "aes-256-gcm"},
    "public_key": "0x...",
    "version": 1
  }

The wallet is encrypted with AES-256-GCM (12-byte IV, 16-byte auth tag)
using a key derived from the password with PBKDF2-HMAC-SHA256 (200k iters).

The private key is decrypted only inside the host process — it never leaves
the agent. The dashboard never sees the key; it only sees the address.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Any

from eth_account import Account
from web3 import Web3

# pycryptodome is a hard runtime requirement: every keystore decrypt path
# uses AES-256-GCM. We hoist the import so a missing dep fails at module
# load (loud, traceable) rather than at first-decrypt time (confusing,
# recoverable only by reading the log). The dep is now also declared in
# pyproject.toml so `pip install bnbagent` is sufficient.
from Crypto.Cipher import AES  # noqa: E402  (hoisted for early failure)

# --- AES-GCM ---------------------------------------------------------------
def _aes_gcm_encrypt(key: bytes, plaintext: bytes) -> tuple[bytes, bytes]:
    """Returns (ciphertext_with_tag, iv)."""
    iv = secrets.token_bytes(12)
    cipher = AES.new(key, AES.MODE_GCM, nonce=iv)
    ct, tag = cipher.encrypt_and_digest(plaintext)
    return ct + tag, iv


def _aes_gcm_decrypt(key: bytes, ct_with_tag: bytes, iv: bytes) -> bytes:
    cipher = AES.new(key, AES.MODE_GCM, nonce=iv)
    ct, tag = ct_with_tag[:-16], ct_with_tag[-16:]
    return cipher.decrypt_and_verify(ct, tag)


# --- keystore I/O ----------------------------------------------------------

PBKDF2_ITERS = 200_000


def _derive_key(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERS, dklen=32)


def _keystore_path() -> Path:
    p = Path(os.environ.get("TWAK_KEYSTORE", "~/.twak/wallet.json")).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def create_keystore(password: str, account: Account | None = None) -> dict:
    """Generate a new key, encrypt with `password`, write to disk, return summary.

    Returns a dict safe to surface to the dashboard — only the address, never
    the private key.
    """
    if not password or len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    acct = account or Account.create()
    salt = secrets.token_bytes(16)
    key = _derive_key(password, salt)
    ct, iv = _aes_gcm_encrypt(key, acct.key)
    blob = {
        "address": Web3.to_checksum_address(acct.address),
        "encrypted": {
            "ciphertext": "0x" + ct.hex(),
            "iv":         "0x" + iv.hex(),
            "salt":       "0x" + salt.hex(),
            "iterations": PBKDF2_ITERS,
            "kdf":        "pbkdf2-sha256",
            "cipher":     "aes-256-gcm",
        },
        "public_key": "0x" + acct._key_obj.public_key.to_bytes().hex(),
        "version": 1,
    }
    _keystore_path().write_text(json.dumps(blob, indent=2))
    os.chmod(_keystore_path(), 0o600)
    return {
        "address":   blob["address"],
        "path":      str(_keystore_path()),
        "public_key": blob["public_key"],
    }


def import_keystore(private_key_hex: str, password: str) -> dict:
    """Encrypt an existing private key into a TWAK keystore. Returns summary."""
    pk = private_key_hex[2:] if private_key_hex.startswith("0x") else private_key_hex
    if len(pk) != 64:
        raise ValueError("private key must be 32 bytes (64 hex chars)")
    key_bytes = bytes.fromhex(pk)
    acct = Account.from_key(key_bytes)
    return create_keystore(password, account=acct)


def decrypt_keystore(blob: dict, password: str) -> bytes:
    """Decrypt the keystore's encrypted private key. Returns raw 32-byte key."""
    enc = blob["encrypted"]
    salt = bytes.fromhex(enc["salt"][2:] if enc["salt"].startswith("0x") else enc["salt"])
    iv   = bytes.fromhex(enc["iv"][2:]   if enc["iv"].startswith("0x")   else enc["iv"])
    ct   = bytes.fromhex(enc["ciphertext"][2:] if enc["ciphertext"].startswith("0x") else enc["ciphertext"])
    iters = enc.get("iterations", PBKDF2_ITERS)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iters, dklen=32)
    return _aes_gcm_decrypt(key, ct, iv)


def load_keystore_summary() -> dict | None:
    """Return the wallet address + path if a keystore exists, else None."""
    p = _keystore_path()
    if not p.exists():
        return None
    try:
        blob = json.loads(p.read_text())
        return {
            "address":    blob.get("address"),
            "path":       str(p),
            "public_key": blob.get("public_key"),
            "version":    blob.get("version"),
        }
    except Exception:
        return None


def unlock_and_get_account(password: str) -> Account:
    """Decrypt the keystore and return an eth_account Account object."""
    p = _keystore_path()
    if not p.exists():
        raise RuntimeError(f"no keystore at {p}")
    blob = json.loads(p.read_text())
    key = decrypt_keystore(blob, password)
    return Account.from_key(key)
