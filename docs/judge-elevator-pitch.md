# BNB Agent — Judge Elevator Pitches

> Three lengths. Use the 2-sentence one for the DoraHacks form "elevator pitch" field. Use the 30-second one as your spoken opener for the demo video or a live judge Q&A. Use the 90-second one if a judge wants the architecture before the demo starts.

---

## 2-sentence pitch (DoraHacks form)

> BNB Agent is a fully autonomous BSC trading agent with a 3-layer LLM safety team — the user signs one policy, the agent signs every transaction, and an on-chain AI identity (ERC-8004) + job escrow (ERC-8183) record every move. It runs three strategies in parallel (delta-neutral funding carry, CMC-driven DEX momentum, mean-reversion) with a daily 3% circuit breaker, pays for its own market data via x402, and exposes 11 MCP tools so other agents can call it.

---

## 30-second spoken pitch (demo video, first 10 seconds)

> "Hi, I'm Blaze. I built BNB Agent — a production-ready, fully autonomous BSC trading agent for the BNB HACK 2026. You sign one user policy, the agent signs every transaction, and a 3-layer LLM safety team gates every trade. Strategy: three sleeves — 70% delta-neutral funding carry, 20% CMC-driven momentum, 10% mean-reversion — with a 3% daily circuit breaker and a curated 149-token allowlist pinned from the contest page. Every tx is signed with Trust Wallet's Agent Kit, the agent has an ERC-8004 identity NFT, and every evaluation window opens 4 ERC-8183 jobs you can verify on BscScan. Let me show you it running live."

---

## 90-second architecture pitch (judge Q&A)

> "BNB Agent is a self-trading, self-registering, self-billing autonomous agent on BNB Smart Chain. You sign **one** YAML user policy — per-trade cap 1%, daily circuit breaker 3%, leverage 2x, position 15%, token allowlist — and the agent runs for a week. The deterministic trading engine is a Python runtime. Every trade goes through three layers: a per-trade risk check, a Layer-2 LLM reviewer that can only VETO (with a hard timeout and a heuristic fallback if the LLM is slow), and a Trust Wallet signing step. The LLM agent team is also three layers: an advisor that runs every 5 minutes and can only TIGHTEN the policy (never loosen it), the per-trade reviewer, and a chat layer that can only RECOMMEND — every recommendation requires a fresh user signature.
>
> The agent pays for its own market data via x402 — every CMC quote is a microcharge, and the dashboard has a full microcharge ledger. It registers itself on-chain via ERC-8004 (the agent's identity NFT) and escrows its PnL via ERC-8183 (one job per sleeve per evaluation window). The Token Module is a real product: ERC-20 deploy on BSC, with CMC-enriched metadata and a single-file HTML landing page. Other agents can call BNB Agent via the MCP server — 11 tools, stdio or SSE.
>
> Deep integration with the three required layers: CoinMarketCap (Data API + Data MCP + Skills + x402), Trust Wallet Agent Kit (every tx, including the chat-deployed tokens, never exposes the key), BNB AI Agent SDK (BSC + perps + ERC-8004 + ERC-8183 + x402). The on-chain evidence — the 8004 NFT, the 8183 jobs, the microcharge ledger, the full signed-tx list — is all public and BscScan-verifiable. The repo is MIT, the backtest reports are committed, the demo script is locked to the JSONs by a meta-test. That's BNB Agent."

---

## Differentiation in 1 line

> **"Most agents are prompts. This one is a runtime — deterministic code, on-chain identity, signed-everywhere, gates-everywhere, with three LLM layers that can only ADD safety, never remove it."**

---

## What I'd cut if a judge looks confused

- Don't lead with the 3 sleeves. Lead with **"you sign one policy, the agent runs for a week"** — that lands the autonomy story.
- Don't lead with ERC-8004/8183. Lead with **"every move is on-chain and BscScan-verifiable"** — same meaning, less jargon.
- Don't lead with x402. Lead with **"it pays for its own data"** — the spend-is-real story is more visceral than the protocol.
- The LLM safety team is a *feature* ("3-layer LLM safety") not a *novelty* ("look at my LLM architecture"). Lead with the constraint (can only TIGHTEN/VETO/RECOMMEND), not the capability.
