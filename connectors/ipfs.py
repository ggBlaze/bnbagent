"""Local IPFS client wrapper (for pinning policy, metadata, deliverable specs)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)


class IPFSClient:
    def __init__(self, api: str = "/ip4/127.0.0.1/tcp/5001", mode: str = "testnet"):
        self.api = api
        self.mode = mode
        self._url = api.replace("/ip4/", "http://").replace("/tcp/", ":")
        self._client = httpx.Client(timeout=10)
        self._local_store: dict[str, bytes] = {}    # in-memory fallback

    def add_json(self, obj: Any) -> str:
        if self.mode in ("testnet", "replay"):
            blob = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
            cid = "Qm" + hashlib.sha256(blob).hexdigest()[:44]
            self._local_store[cid] = blob
            log.debug("ipfs add (local) → %s (%d bytes)", cid, len(blob))
            return cid
        try:
            r = self._client.post(
                f"{self._url}/api/v0/add",
                files={"file": ("blob.json", json.dumps(obj).encode(), "application/json")},
            )
            r.raise_for_status()
            return r.json()["Hash"]
        except Exception as e:
            log.warning("ipfs add failed (%s); falling back to local store", e)
            return self.add_json.__wrapped__(obj) if hasattr(self.add_json, "__wrapped__") else self._local_only(obj)

    def _local_only(self, obj: Any) -> str:
        blob = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
        cid = "Qm" + hashlib.sha256(blob).hexdigest()[:44]
        self._local_store[cid] = blob
        return cid

    def cat(self, cid: str) -> bytes:
        if cid in self._local_store:
            return self._local_store[cid]
        if self.mode in ("testnet", "replay"):
            raise KeyError(f"cid not in local store: {cid}")
        r = self._client.post(f"{self._url}/api/v0/cat?arg={cid}")
        r.raise_for_status()
        return r.content

    def cat_json(self, cid: str) -> Any:
        return json.loads(self.cat(cid))


def from_config(path: str = "config/config.yaml") -> IPFSClient:
    import yaml
    from pathlib import Path
    from core.config_paths import load_config as _load_merged_config, DEFAULT_CONFIG
    # v2.1.1: shadow pattern on the default path; explicit paths verbatim.
    if Path(path) == DEFAULT_CONFIG:
        cfg = _load_merged_config()
    else:
        cfg = yaml.safe_load(open(path))
    return IPFSClient(
        api=os.environ.get("IPFS_API", "/ip4/127.0.0.1/tcp/5001"),
        mode=cfg.get("mode", "testnet"),
    )
