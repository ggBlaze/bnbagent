"""ERC-8183 — submit a sleeve's deliverable.

Computes the deliverable from the portfolio's closed trades, pins it to IPFS,
and calls `submit(jobId, proofCID)`.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from policy.policy_sign import canonical_json
from web3 import Web3

log = logging.getLogger(__name__)


def _sleeve_metrics(sleeve: str, portfolio) -> dict:
    """Build the deliverable.results dict for a sleeve from portfolio state."""
    closed = [t for t in portfolio.closed_trades if t["sleeve"] == sleeve]
    pnl = sum(float(t["pnl_usdc"]) for t in closed)
    wins = sum(1 for t in closed if float(t["pnl_usdc"]) > 0)
    losses = sum(1 for t in closed if float(t["pnl_usdc"]) <= 0)
    hit_rate = wins / max(1, len(closed))
    return {
        "pnl_usdc": f"{pnl:.2f}",
        "trades_count": len(closed),
        "hit_rate": round(hit_rate, 4),
        "wins": wins,
        "losses": losses,
        "trades": closed[-100:],   # last 100 only
    }


def submit_sleeve(sleeve: str, job_id: int, portfolio, policy: dict, ipfs, erc8183) -> str:
    """Build + pin + submit. Returns the IPFS CID of the deliverable."""
    results = _sleeve_metrics(sleeve, portfolio)
    body = {k: v for k, v in policy.items() if k != "signature"}
    policy_hash = "0x" + Web3.keccak(canonical_json(body)).hex()
    payload = {
        "schema":         "bnbagent/deliverable/v1",
        "sleeve":         sleeve,
        "computed_at":    int(time.time()),
        "policy_version": policy.get("version"),
        "policy_hash":    policy_hash,
        "results":        results,
    }
    cid = ipfs.add_json(payload)
    erc8183.submit(job_id, cid)
    log.info(f"submitted job {job_id} for sleeve {sleeve} (cid={cid})")
    return cid


def submit_all(jobs: dict[str, int], portfolio, policy: dict, ipfs, erc8183) -> dict[str, str]:
    return {sleeve: submit_sleeve(sleeve, jid, portfolio, policy, ipfs, erc8183)
            for sleeve, jid in jobs.items()}


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from core.boot import boot
    c = boot()
    cid = submit_sleeve("A", 1, c["portfolio"], c["policy"], c["ipfs"], c["erc8183"])
    print(cid)
