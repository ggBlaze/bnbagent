"""BNB Agent — ERC-8183 job lifecycle.

Per evaluation window, the agent creates 4 jobs (Sleeve A, B, C, Aggregator).
For each, the flow is:
  1. createJob(provider=agent, evaluator=user, specCID, budget=25 USDC)
  2. fund(jobId, 25 USDC)         ← user signs (one-time approval)
  3. submit(jobId, proofCID)      ← agent signs after computing results
  4. complete(jobId)              ← user signs (off-chain review)

The on-chain state machine is stubbed in testnet mode; the real contracts are
deployed at the addresses in config.
"""
