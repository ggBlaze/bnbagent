"""ERC-8004 identity registration.

Builds the agent's metadata JSON, pins it to IPFS, and calls the registry's
`register(string agentURI)` function. In testnet mode, the registration is
stubbed — the metadata is still pinned to IPFS and the agent's identity is
saved locally so the dashboard can show it.
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
        log.info("identity already registered: tokenId=%s", identity.get("token_id"))
        return identity

    meta = json.load(open("identity/metadata.json"))
    meta["attributes"] = [
        a for a in meta["attributes"] if a.get("trait_type") != "version"
    ] + [{"trait_type": "version", "value": version}]
    meta["trust"]["evaluator"] = policy["evaluator_address"]
    meta["endpoints"]["operator"] = wallet.address

    cid = ipfs.add_json(meta)
    token_id, _ = erc8004.register(agent_uri=f"ipfs://{cid}")
    identity = {
        "token_id":     token_id,
        "cid":          cid,
        "agent_address": wallet.address,
        "evaluator_address": policy["evaluator_address"],
        "version":      version,
        "metadata":     meta,
    }
    IDENTITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    json.dump(identity, IDENTITY_PATH.open("w"), indent=2)
    log.info("ERC-8004 identity registered: tokenId=%s cid=%s", token_id, cid)
    return identity


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from core.boot import boot
    components = boot()
    register_agent(components["erc8004"], components["ipfs"], components["wallet"], components["policy"])
    print(json.dumps(json.load(IDENTITY_PATH.open()), indent=2))
