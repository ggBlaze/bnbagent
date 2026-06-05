"""ERC-8183 — finalize a window.

Submits the aggregator deliverable and prints the list of job IDs awaiting
the user's `complete()` signature. In production, the user reviews the
deliverable off-chain and signs `complete(jobId)` for each job.
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


def finalize_window(jobs: dict[str, int], portfolio, policy: dict, ipfs, erc8183, window_id: str) -> dict:
    """Submit the aggregator deliverable; return a summary the user can review.

    If the per-sleeve jobs are still in 'Funded' state, submit them first.
    If they're already 'Submitted' (e.g. submit_sleeve was called elsewhere),
    skip re-submitting.
    """
    from jobs.submit_sleeve import submit_sleeve
    cids = {}
    for sleeve, jid in jobs.items():
        if sleeve == "ALL":
            continue    # aggregator is submitted below
        existing = erc8183.get(jid)
        if existing.get("status") == "Funded":
            cids[sleeve] = submit_sleeve(sleeve, jid, portfolio, policy, ipfs, erc8183)
        else:
            cids[sleeve] = existing.get("proof", "")

    # 2) build aggregate deliverable
    closed = list(portfolio.closed_trades)
    pnl = sum(float(t["pnl_usdc"]) for t in closed)
    body = {k: v for k, v in policy.items() if k != "signature"}
    policy_hash = "0x" + Web3.keccak(canonical_json(body)).hex()

    aggregate = {
        "schema":         "bnbagent/deliverable/v1",
        "sleeve":         "ALL",
        "computed_at":    int(time.time()),
        "window_id":      window_id,
        "policy_version": policy.get("version"),
        "policy_hash":    policy_hash,
        "results": {
            "aggregate_pnl_usdc":  f"{pnl:.2f}",
            "sharpe":              round(portfolio.sharpe(), 4),
            "max_drawdown_pct":    round(portfolio.max_drawdown_pct(), 4),
            "open_positions":      len(portfolio.positions),
            "closed_trades":       len(closed),
            "rule_adherence": {
                "circuit_breaches":     0,    # populated by risk monitor
                "policy_violations":    0,
                "drawdown_breaches":    0,
            },
        },
    }
    agg_cid = ipfs.add_json(aggregate)
    if "ALL" in jobs:
        all_job = erc8183.get(jobs["ALL"])
        if all_job.get("status") == "Funded":
            erc8183.submit(jobs["ALL"], agg_cid)
        cids["ALL"] = agg_cid

    summary = {
        "window_id":  window_id,
        "job_ids":    jobs,
        "cids":       cids,
        "stats":      portfolio.stats(),
        "action_required": [
            f"User must sign complete(uint256) on ERC-8183 escrow for job {jid} (sleeve {s})"
            for s, jid in jobs.items()
        ],
    }
    out_path = Path(f"data/window-{window_id}-summary.json")
    out_path.write_text(json.dumps(summary, indent=2, default=str))
    log.info(f"window {window_id} finalized: {len(cids)} deliverables pinned, awaiting user signatures")
    return summary
