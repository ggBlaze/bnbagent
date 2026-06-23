"""ERC-8004 identity registration.

Builds the agent's metadata JSON, pins it to a public IPFS gateway, and
calls the registry's ``register(string agentURI)`` function. In testnet
mode, the registration is stubbed — the metadata is still pinned to the
local IPFS store and the agent's identity is saved locally so the
dashboard can show it.

v2.3.0: uses ``IPFSClient.pin_to_public_gateway`` so the agentURI is
HTTP-resolvable from a public gateway (Pinata if PINATA_API_KEY is set).
This is what 8004scan.io's crawler needs to display metadata on the
agent's profile page.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

IDENTITY_PATH = Path("~/.bnbagent/identity.json").expanduser()


def register_agent(erc8004, ipfs, wallet, policy: dict, version: str = "1.0.0") -> dict:
    """Idempotent — re-uses ~/.bnbagent/identity.json if present."""
    if IDENTITY_PATH.exists():
        identity = json.load(IDENTITY_PATH.open())
        # v2.3.0: also validate the saved identity points at the registry
        # 8004scan.io indexes. If it was registered against the old
        # placeholder (0x212c61b... CompetitionRegistry), re-register
        # against the canonical IdentityRegistry.
        if (
            identity.get("registry_address", "").lower()
            != "0x8004a169fb4a3325136eb29fa0ceb6d2e539a432"
            and identity.get("agent_address", "").lower() == wallet.address.lower()
        ):
            log.warning(
                "saved identity was registered against %s (not the 8004scan-indexed "
                "IdentityRegistry). Backing up and re-registering on-chain.",
                identity.get("registry_address"),
            )
            backup = IDENTITY_PATH.with_suffix(".json.bak")
            os.rename(IDENTITY_PATH, backup)
        else:
            log.info("identity already registered: tokenId=%s", identity.get("token_id"))
            return identity

    meta = json.load(open("identity/metadata.json"))
    meta["attributes"] = [
        a for a in meta["attributes"] if a.get("trait_type") != "version"
    ] + [{"trait_type": "version", "value": version}]
    meta["trust"]["evaluator"] = policy["evaluator_address"]
    meta["endpoints"]["operator"] = wallet.address

    # v2.3.0: pin to a public gateway so 8004scan.io's crawler can HTTP-GET
    # the metadata via tokenURI(tokenId) → agentURI → metadata JSON.
    cid, gateway_url = ipfs.pin_to_public_gateway(meta)
    log.info("identity pinned: cid=%s gateway_url=%s", cid, gateway_url)
    token_id, _ = erc8004.register(agent_uri=gateway_url)
    identity = {
        "token_id":     token_id,
        "cid":          cid,
        "agent_uri":    gateway_url,
        "agent_address": wallet.address,
        "registry_address": erc8004.registry,
        "tx_hash":      getattr(erc8004, "tx_hash", None),
        "evaluator_address": policy["evaluator_address"],
        "version":      version,
        "metadata":     meta,
    }
    IDENTITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    json.dump(identity, IDENTITY_PATH.open("w"), indent=2)
    log.info(
        "ERC-8004 identity registered: tokenId=%s cid=%s registry=%s tx=%s",
        token_id, cid, erc8004.registry, identity["tx_hash"],
    )
    return identity


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from core.boot import boot
    components = boot()
    register_agent(components["erc8004"], components["ipfs"], components["wallet"], components["policy"])
    print(json.dumps(json.load(IDENTITY_PATH.open()), indent=2))
