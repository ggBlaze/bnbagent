# BNB Agent — On-Chain Identity & Commerce

BNB Agent uses two emerging EVM standards to put its identity and its work
on-chain, both on BNB Smart Chain:

- **ERC-8004** — verifiable on-chain identity for AI agents (identity NFT)
- **ERC-8183** — agentic commerce via job escrow with evaluator attestation

## ERC-8004 — Agent identity

At startup, the agent mints itself an identity NFT. The metadata describes
who the agent is, what strategy it runs, and what its risk profile is.

**Metadata structure (pinned to IPFS):**
```json
{
  "name": "BNB Agent",
  "description": "...",
  "attributes": [
    {"trait_type": "strategy",  "value": "three-sleeve-ensemble"},
    {"trait_type": "sleeves",   "value": ["A:funding-carry","B:dex-momentum","C:mean-reversion"]},
    {"trait_type": "max_gross_leverage","value": 2.0},
    {"trait_type": "per_trade_risk",     "value": "1.0%"},
    {"trait_type": "daily_loss_cap",     "value": "3.0%"},
    {"trait_type": "version",            "value": "1.0.0"}
  ],
  "endpoints": { "metrics": "...", "policy": "ipfs://..." },
  "trust":     { "evaluator": "0x...", "schema": "erc-8004-v0" }
}
```

**Registration flow:**
1. Build the metadata JSON
2. Pin to IPFS (local node in testnet; real IPFS in mainnet)
3. Call `register(string agentURI)` on the ERC-8004 registry
4. Save `{tokenId, cid, txHash}` to `~/.bnbagent/identity.json`

The agent then has a permanent, verifiable on-chain identity.

## ERC-8183 — Job lifecycle

For each evaluation window, BNB Agent opens 4 jobs (one per sleeve + an
aggregator). Each job is an on-chain escrowed work order with a deliverable
spec, a budget, and an evaluator.

**Per-window jobs:**

| Sleeve | Budget | Provider | Evaluator | Deliverable |
|---|---|---|---|---|
| A — funding carry | 25 USDC | agent | user | funding APR, PnL, max DD, positions, breach count |
| B — momentum | 25 USDC | agent | user | trade list, hit rate, Sharpe, exits breakdown |
| C — mean-rev | 25 USDC | agent | user | trade list, z-score at entry, hit rate, Sharpe |
| ALL — aggregator | 25 USDC | agent | user | aggregate PnL, Sharpe, max DD, rule-adherence score |

**State machine:**
```
   Open ──fund()──> Funded ──submit(proof)──> Submitted ──complete()──> Completed
                       │                            │
                       └──claimRefund()──> Refunded └──reject()──> Rejected
```

**At window end:**
1. Agent computes the deliverable for each sleeve
2. Pins the deliverable to IPFS
3. Calls `submit(jobId, proofCID)`
4. Builds an aggregator deliverable
5. Submits the aggregator
6. User reviews the deliverables off-chain
7. User signs `complete(uint256 jobId)` for each job → USDC released to agent

**Why this is on-chain:**
- The evaluator (user) holds the power — they can reject any job
- The provider (agent) cannot take the funds until the evaluator signs off
- The deliverable spec and proof are publicly verifiable via IPFS + BscScan
- This is real on-chain commerce, not a stub
