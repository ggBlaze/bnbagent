"""Local IPFS client wrapper (for pinning policy, metadata, deliverable specs).

v2.3.0: added `pin_to_public_gateway()` for ERC-8004 agentURI resolution.
8004scan.io crawlers HTTP-GET the agentURI after the IdentityRegistry's
tokenURI() returns it. For the agent to be discoverable on 8004scan with
fetchable metadata, the agentURI needs to resolve from a public gateway.

Strategy (in order):
  1. PINATA_API_KEY set → POST to api.pinata.cloud/pinning/pinJSONToIPFS,
     return the gateway URL `https://gateway.pinata.cloud/ipfs/{cid}`.
  2. Local IPFS daemon running → pin via /api/v0/add, return
     `http://{IPFS_GATEWAY}/ipfs/{cid}` (defaults to
     https://ipfs.io/ipfs/{cid}).
  3. Fallback → local CID only (ipfs://{cid}). The NFT will still be
     indexed on 8004scan, but the metadata panel will show "metadata
     unavailable" until someone pins it.
"""
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
        # v2.3.0: optional Pinata pinning for ERC-8004 agentURI metadata.
        # Reading at construction so behaviour is consistent for the
        # lifetime of the process.
        #
        # Pinata auth — three acceptable shapes (auto-detected):
        #   1. PINATA_JWT set → Bearer token (modern, recommended)
        #   2. PINATA_API_KEY + PINATA_SECRET_API_KEY → legacy auth
        #   3. Neither → no Pinata path, falls through to daemon/local
        self._pinata_jwt = os.environ.get("PINATA_JWT", "").strip()
        self._pinata_key = os.environ.get("PINATA_API_KEY", "").strip()
        self._pinata_secret = os.environ.get("PINATA_SECRET_API_KEY", "").strip()
        self._public_gateway = os.environ.get(
            "IPFS_PUBLIC_GATEWAY", "https://gateway.pinata.cloud"
        ).rstrip("/")

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

    # ------------------------------------------------------------------
    # v2.3.0: public-gateway pinning for ERC-8004 agentURI metadata
    # ------------------------------------------------------------------

    def pin_to_public_gateway(self, obj: Any) -> tuple[str, str]:
        """Pin `obj` to a public-resolvable IPFS gateway.

        Returns ``(cid, gateway_url)`` where ``gateway_url`` is an
        HTTPS URL the ERC-8004 IdentityRegistry's ``tokenURI(tokenId)``
        will return so 8004scan.io's crawler can fetch the metadata.

        Strategy:
          1. Pinata: requires PINATA_API_KEY (and optionally
             PINATA_SECRET_API_KEY). Returns the Pinata gateway URL
             (`https://gateway.pinata.cloud/ipfs/{cid}`).
          2. Local IPFS daemon: if reachable, pin via /api/v0/add and
             return the configured IPFS_PUBLIC_GATEWAY URL.
          3. Fallback: compute a deterministic CID locally and return
             just the `ipfs://{cid}` form. The NFT is still indexed on
             8004scan; only the metadata panel is unavailable.

        Never raises — failures degrade gracefully with a logged warning.
        """
        blob = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
        # 1. Pinata (preferred — most reliable for 8004scan crawler)
        # JWT takes precedence over legacy (key, secret) auth.
        if self._pinata_jwt:
            try:
                headers = {"Authorization": f"Bearer {self._pinata_jwt}"}
                r = httpx.post(
                    "https://api.pinata.cloud/pinning/pinJSONToIPFS",
                    headers=headers,
                    json={
                        "pinataContent": obj,
                        "pinataMetadata": {"name": "bnbagent-identity.json"},
                    },
                    timeout=15,
                )
                r.raise_for_status()
                cid = r.json()["IpfsHash"]
                url = f"{self._public_gateway}/ipfs/{cid}"
                log.info("pinata (jwt) pin ok → cid=%s url=%s", cid, url)
                self._local_store[cid] = blob
                return cid, url
            except Exception as e:
                log.warning("pinata (jwt) pin failed (%s); falling back", e)
        elif self._pinata_key and self._pinata_secret:
            try:
                headers = {
                    "pinata_api_key": self._pinata_key,
                    "pinata_secret_api_key": self._pinata_secret,
                }
                r = httpx.post(
                    "https://api.pinata.cloud/pinning/pinJSONToIPFS",
                    headers=headers,
                    json={
                        "pinataContent": obj,
                        "pinataMetadata": {"name": "bnbagent-identity.json"},
                    },
                    timeout=15,
                )
                r.raise_for_status()
                cid = r.json()["IpfsHash"]
                url = f"{self._public_gateway}/ipfs/{cid}"
                log.info("pinata (key+secret) pin ok → cid=%s url=%s", cid, url)
                self._local_store[cid] = blob
                return cid, url
            except Exception as e:
                log.warning("pinata (key+secret) pin failed (%s); falling back", e)
        # 2. Local IPFS daemon
        if self.mode not in ("testnet", "replay"):
            try:
                r = self._client.post(
                    f"{self._url}/api/v0/add?pin=true",
                    files={"file": ("blob.json", blob, "application/json")},
                )
                r.raise_for_status()
                cid = r.json()["Hash"]
                url = f"{self._public_gateway}/ipfs/{cid}"
                log.info("ipfs daemon pin ok → cid=%s url=%s", cid, url)
                self._local_store[cid] = blob
                return cid, url
            except Exception as e:
                log.warning("ipfs daemon pin failed (%s); using local CID only", e)
        # 3. Fallback — local CID, no gateway. NFT still indexed.
        cid = "Qm" + hashlib.sha256(blob).hexdigest()[:44]
        self._local_store[cid] = blob
        log.warning(
            "no public IPFS gateway configured (set PINATA_API_KEY or run "
            "an IPFS daemon). Agent NFT will be indexed on 8004scan but "
            "metadata will show 'unavailable' until the CID is pinned."
        )
        return cid, f"ipfs://{cid}"


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
