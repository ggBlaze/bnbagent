"""ERC-8183 — open jobs for a new evaluation window.

For each sleeve + the aggregator, pin a deliverable spec to IPFS, then call
`createJob(provider, evaluator, specCID, budget, token)` and `fund(jobId, amount)`.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

JOB_SPECS = {
    "A": {
        "schema": "bnbagent/deliverable-spec/v1",
        "sleeve": "A",
        "name": "Funding-rate carry",
        "description": "Delta-neutral funding carry on curated top-20 BSC basket.",
        "metrics_required": [
            "funding_apr", "pnl_usdc", "max_drawdown", "basis_breach_count",
            "funding_paid_usdc", "positions_count", "venue_selected",
        ],
    },
    "B": {
        "schema": "bnbagent/deliverable-spec/v1",
        "sleeve": "B",
        "name": "DEX momentum",
        "description": "CMC-signal-driven momentum on BNB-chain DEX pairs.",
        "metrics_required": [
            "trades", "hit_rate", "sharpe", "max_consecutive_losses",
            "tp_exits", "stop_exits", "time_exits",
        ],
    },
    "C": {
        "schema": "bnbagent/deliverable-spec/v1",
        "sleeve": "C",
        "name": "Mean-reversion",
        "description": "Fade 1h drops >2.5σ on top-20 BSC tokens.",
        "metrics_required": [
            "trades", "hit_rate", "sharpe", "zscore_at_entry",
            "tp_exits", "stop_exits",
        ],
    },
    "ALL": {
        "schema": "bnbagent/deliverable-spec/v1",
        "sleeve": "ALL",
        "name": "Aggregator",
        "description": "Aggregate PnL + Sharpe + max DD + rule-adherence across sleeves.",
        "metrics_required": [
            "aggregate_pnl_usdc", "sharpe", "max_drawdown",
            "rule_adherence_score", "sleeve_attribution",
        ],
    },
}


def open_jobs_for_window(
    window_id: str,
    policy: dict,
    erc8183,
    ipfs,
    wallet,
    usdc_address: str,
    budget_per_job_usdc: int = 25,
) -> dict[str, int]:
    """Create + fund jobs for A, B, C, and ALL. Returns {sleeve: jobId}."""
    evaluator = policy["evaluator_address"]
    budget = budget_per_job_usdc * 10**6   # USDC 6 decimals
    job_ids: dict[str, int] = {}

    for sleeve, spec in JOB_SPECS.items():
        spec_cid = ipfs.add_json({**spec, "window_id": window_id})
        # bytes32 deliverable spec: truncate cid hash
        spec_bytes = bytes.fromhex(spec_cid[2:].ljust(64, "0")[:64])
        job_id = erc8183.create_job(
            provider=wallet.address,
            evaluator=evaluator,
            deliverable_spec=spec_bytes,
            budget=budget,
            token=usdc_address,
        )
        erc8183.fund(job_id, budget)
        job_ids[sleeve] = job_id
        log.info(f"opened job {job_id} for sleeve {sleeve} (spec={spec_cid})")

    # persist for the window
    out_path = Path(f"data/jobs-{window_id}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "window_id": window_id,
        "policy_version": policy.get("version"),
        "job_ids": job_ids,
        "evaluator": evaluator,
        "agent": wallet.address,
    }, indent=2))
    return job_ids


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from core.boot import boot
    from jobs.open_jobs import open_jobs_for_window

    c = boot()
    job_ids = open_jobs_for_window(
        window_id=f"dev-{int(time.time())}",
        policy=c["policy"],
        erc8183=c["erc8183"],
        ipfs=c["ipfs"],
        wallet=c["wallet"],
        usdc_address=c["config"]["tokens"]["USDC"]["bsc_address"] if isinstance(c["config"]["tokens"]["USDC"], dict) else c["config"]["tokens"]["USDC"],
    )
    print(json.dumps(job_ids, indent=2))
