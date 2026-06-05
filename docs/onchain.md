# BNB Agent — On-Chain Identity & Commerce

BNB Agent uses two emerging EVM standards to put its identity and its work
on-chain, both on BNB Smart Chain:

- **ERC-8004** — verifiable on-chain identity for AI agents (identity NFT)
- **ERC-8183** — agentic commerce via job escrow with evaluator attestation

## ERC-8004 — Agent identity

At startup, the agent mints itself an identity NFT. The metadata describes
who the agent is, what strategy it runs, what its risk profile is, and
(v2.0) the SHA-256 hashes of the canonical pro personas.

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
    {"trait_type": "version",            "value": "2.0.0"},
    {"trait_type": "ai_agent_team",      "value": ["advisor","reviewer","chat","token_module"]},
    {"trait_type": "skills",             "value": ["telegram_alert","farcaster_post","webhook_dispatch","x_sentiment","cmc_global_filter","glassnode_onchain"]},
    {"trait_type": "mcp_tools",          "value": ["bnbagent_get_pnl","bnbagent_list_positions","bnbagent_list_trades","bnbagent_get_policy","bnbagent_recommend_risk_change","bnbagent_deploy_token","bnbagent_chat","bnbagent_list_skills","bnbagent_enable_skill","bnbagent_disable_skill"]}
  ],
  "endpoints": { "metrics": "...", "policy": "ipfs://..." },
  "personas_pro_sha256": {
    "advisor":      "a4f8...c2d1",
    "reviewer":     "b2c9...e7f0",
    "chat":         "c1d4...a8b2",
    "token_module": "d7e2...f1c5"
  },
  "trust":     { "evaluator": "0x...", "schema": "erc-8004-v0" }
}
```

**Registration flow:**
1. Build the metadata JSON (with persona hashes)
2. Pin to IPFS (local node in testnet; real IPFS in mainnet)
3. Call `register(string agentURI)` on the ERC-8004 registry
4. Save `{tokenId, cid, txHash}` to `~/.bnbagent/identity.json`

The agent then has a permanent, verifiable on-chain identity. A remote
MCP client can call `bnbagent_get_policy` and verify the persona hashes
against the on-chain metadata — this is what makes the personas
"auditable by anyone".

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

## v2.0 additions to the on-chain story

- **Persona hashes in the ERC-8004 metadata** — anyone can verify the
  agent's pro personas are the canonical stock. A remote agent (or a
  judge) can pull the IPFS metadata, compare the persona SHA-256s
  against the on-disk `agents/_pro_defaults/`, and confirm the agent
  is unmodified.
- **Skills + MCP tools in the metadata** — the on-chain record now
  advertises the 6 Skills and 10 MCP tools, making the agent's
  capabilities verifiable.
- **TokenModule deploys pin to IPFS** — each token deployment
  produces a `TokenDeployResult` with `ipfs_metadata_cid`, and the
  metadata is uploaded to IPFS via the agent's existing IPFS client.
  Judges and other agents can fetch the token's metadata from
  `ipfs://<cid>` and verify the deploy params + tx hash.
