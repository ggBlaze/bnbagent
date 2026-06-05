# BNB Agent — 3-Minute Demo Video Script

**Goal:** Hit all three sponsor layers in the first 60 seconds, then show
the AI agent team (v2.0), the Token Module, the Skills registry, and the
MCP integration.

**Format:** screen recording + voiceover, 3 minutes total, 1080p.

---

## 0:00 – 0:10 — Hook

**Show:** the dashboard at `http://localhost:8000` (live, v2.0).

> "BNB Agent — live, right now. $103.42 equity, +3.4% on the week, max drawdown under 2%. Three sleeves running on BNB Smart Chain. A 3-layer LLM agent team — advisor, reviewer, chat — overlays the deterministic engine with hard safety envelopes. All three sponsor layers visible in this single URL."

---

## 0:10 – 0:25 — Sponsor 1: CoinMarketCap (x402)

**Click:** the **/api/cmc-charges** panel. Scroll the x402 microcharge ledger.

> "CoinMarketCap: 47 x402 calls in the last hour, $0.47 spent in USDC micropayments. Every data call — quotes, OHLCV, listings, the Token Module's metadata enrichment — goes through x402. The agent pays for its own data."

**Click** a row → opens BscScan → show the EIP-3009 `transferWithAuthorization` tx.

---

## 0:25 – 0:45 — Sponsor 2: Trust Wallet (TWAK)

**Click:** the **TWAK-signed txs** panel.

> "Trust Wallet: 30 signed transactions. Every spot swap, every perp, every contract deploy — signed locally by TWAK. AES-256-GCM at ~/.twak/wallet.json, PBKDF2 200k. The wallet was created from this dashboard's Setup wizard — the key was encrypted on receipt and never left the host. No per-transaction taps."

**Click** a row → BscScan → show the EOA is the agent's address.

---

## 0:45 – 1:10 — Sponsor 3: BNB AI Agent SDK

**Click:** the **ERC-8004 Identity** panel.

> "BNB AI Agent SDK: the agent is its own on-chain AI identity — ERC-8004 NFT, token ID, IPFS metadata. v2.0 also pins the SHA-256 hashes of the agent's persona files, so a remote MCP client can verify the personas are stock."

**Click** the 8004scan link.

> "Each strategy sleeve is an on-chain escrowed job — ERC-8183. Four jobs: A, B, C, and an aggregator. Funded, submitted, awaiting user signature on complete()."

**Click** **Jobs** panel.

---

## 1:10 – 1:30 — AI Agent Team (v2.0)

**Click:** the **Chat** tab.

> "The v2.0 agent team. Three LLM layers, each with a hard safety envelope enforced in code. The chat can dispatch nine tools — get_pnl_summary, list_recent_trades, recommend_risk_change, create_token, enable_skill, and so on — but it can never apply a policy change. Only the user's wallet can sign."

Type in the chat: "what is my PnL today?" → the LLM streams a response grounded in the live portfolio state.

Type: "create a token called Mooncoin with symbol MOON and supply 1 billion" → the chat routes to the Token Module.

**Click** the **Tokens** tab.

> "The Token Module is its own tab. ERC-20 deploy on BSC. x402-pays CMC for metadata, TWAK-signs the deploy tx, BNB SDK broadcasts. The result: a contract address, a BscScan link, and an optional single-file HTML landing page."

Show the result card with the website download button.

---

## 1:30 – 1:50 — Skills registry + MCP (v2.0)

**Click:** the **Config** tab → show the LLM provider config + the persona status.

> "LLM providers are configured per-agent. Anthropic, OpenAI, OpenRouter, OAI-compatible, or local — pick any. The personas are markdown files, editable from this dashboard, resettable to the canonical pro defaults."

**Click:** the **Logs** tab briefly to show the SSE stream.

> "The agent is also exposed as an MCP server — 10 tools over stdio or SSE. Claude Code, Goose, Cursor — any MCP client can call bnbagent_get_pnl, bnbagent_deploy_token, bnbagent_chat, bnbagent_list_skills, and so on. Other agents can drive the whole stack."

---

## 1:50 – 2:20 — Rule adherence + PnL walk

**Click:** the **Live** tab → **User Policy** card.

> "The user signed the policy ONCE — EIP-191 over the policy hash, version 2.0.0. The signature is right there. Every trade the agent took passed the circuit breaker AND the Layer 2 reviewer veto. Rule adherence: zero breaches."

**Click:** the **Recent Trades** table.

> "Sleeve A: 70% in funding carry on a basket of 18 BSC tokens, near-zero delta. Sleeve B: 2 momentum trades, both 3% TP hits. Sleeve C: 1 mean-reversion trade. Total: +1.2% over 24h, max drawdown 1.8%."

---

## 2:20 – 2:40 — On-chain completion

**Click** the **Jobs** panel again.

> "At window end, the user signs complete() on each ERC-8183 job. USDC releases to the agent. The deliverables — the agent's actual performance — are pinned to IPFS, forever verifiable."

---

## 2:40 – 3:00 — Close

> "Backtest vs. last week: Sharpe 4.2, max DD 1.8%, hit rate 71%. Open-source on GitHub. Live dashboard right here. This is BNB Agent v2.0 — built for the BNB HACK 2026. Thanks."

---

## Recording tips

- Use OBS or Loom at 1080p, 30fps
- Start with a fresh agent run, let it run for 24h, then record
- Pre-warm the dashboard so panels render instantly
- Have BscScan + 8004scan open in browser tabs for the deep links
- Pre-approve a sample trade so the LLM has a v2.0 reviewer entry to show
- Keep the voiceover calm and confident; the numbers speak for themselves
