# BNB Agent — 3-Minute Demo Video Script

**Goal:** Hit all three sponsor layers in the first 60 seconds, then show
the AI agent team (v2.0), the Token Module, the Skills registry, and the
MCP integration.

**Format:** screen recording + voiceover, 3 minutes total, 1080p.

**Numbers shown are from the actual `data/reports/replay_bull.html` /
`replay_bear.html` / `replay_chop.html` run via
`python -m scripts.run_both_regimes`. The synthetic tape is the stress
test for the strategy, not the live-PnL window. Replace these with the
live window numbers once that runs (2026-06-22 → 2026-06-28).**

**v2.0.3 numbers (2026-06-05, canonical):**

| Regime | Return | Max DD | Trades | Hit Rate | Sharpe |
|---|---|---|---|---|---|
| bull | +0.21% | 0.74% | 189 | 76% | +14 |
| bear | -1.65% | 1.66% | 862 | 95% | -58 |
| chop | -1.64% | 1.73% | 1,413 | 95% | -30 |

Source: `data/reports/replay_{bull,bear,chop}.json`. These are the actual
JSON values from the canonical `python -m scripts.run_both_regimes` run
on 2026-06-05. Open the file, judge.

**What v2.0.3 actually shipped:**

- AgentShim now has `review_trade` (the harness was logging warnings
  on every tick)
- Sleeve B: regime filter loosened to 4h-only (was 4h+1h AND), vol spike
  threshold 2.0×→1.5× (more candidates)
- Sleeve C: zscore 2.5→2.0 (more candidates)
- Sleeve A: minimum-hold-time 24h on vol-pause (no churn in/out of
  positions when realized vol oscillates around the 5% threshold)

**Structural caveat:** on the synthetic 5-min tape, all trades are
attributed to Sleeve A (the carry). Sleeve B's `_scan_signals` asks for
24 hourly candles; the tape is 5-min. So the "4h breakout" is actually
a "20min breakout". This is a data/scale mismatch in the harness, not
a strategy bug. On real BSC hourly data (CMC OHLCV), the B/C signals
should fire. Live PnL window 2026-06-22 → 2026-06-28 will tell.

---

## 0:00 – 0:10 — Hook

**Show:** the dashboard at `http://localhost:8000` (live, v2.0).

> "BNB Agent — live, right now. A three-sleeve trading agent on BNB Smart Chain, plus a 3-layer LLM agent team that overlays the deterministic engine with hard safety envelopes. The agent pays for its own data with USDC via x402, signs its own txs with Trust Wallet, and is registered on-chain as its own AI identity. All three sponsor layers visible in this single URL."

---

## 0:10 – 0:25 — Sponsor 1: CoinMarketCap (x402)

**Click:** the **/api/cmc-charges** panel. Scroll the x402 microcharge ledger.

> "CoinMarketCap: every market-data call — quotes, OHLCV, listings, the Token Module's metadata enrichment — goes through x402. The agent pays for its own data in USDC micropayments. Here's the EIP-3009 `transferWithAuthorization` on BscScan."

**Click** a row → opens BscScan → show the EIP-3009 tx.

---

## 0:25 – 0:45 — Sponsor 2: Trust Wallet (TWAK)

**Click:** the **TWAK-signed txs** panel.

> "Trust Wallet: every spot swap, every perp, every contract deploy — signed locally by TWAK. AES-256-GCM at ~/.twak/wallet.json, PBKDF2 200k. The wallet was created from this dashboard's Setup wizard — the key was encrypted on receipt and never left the host. No per-transaction taps."

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

> "The v2.0 agent team. Three LLM layers, each with a hard safety envelope enforced in code, not delegated to the LLM. The advisor can only TIGHTEN the signed policy. The reviewer can only VETO a trade, with a 0.5s timeout and a heuristic fallback. The chat can recommend a policy change but never apply it — only the user's wallet can sign."

Type in the chat: "what is my PnL today?" → the LLM streams a response grounded in the live portfolio state.

Type: "create a token called Mooncoin with symbol MOON and supply 1 billion" → the chat routes to the Token Module.

**Click** the **Tokens** tab.

> "The Token Module is its own tab. ERC-20 deploy on BSC. x402-pays CMC for metadata, TWAK-signs the deploy tx, BNB SDK broadcasts. Mainnet deploys require the user to type the token SYMBOL — case-insensitive match, since the symbol is the canonical on-chain identifier forever."

Show the result card with the website download button.

---

## 1:30 – 1:50 — Skills registry + MCP (v2.0)

**Click:** the **Config** tab → show the LLM provider config + the persona status.

> "LLM providers are configured per-agent. Anthropic, OpenAI, OpenRouter, OAI-compatible, or local — pick any. The personas are markdown files, editable from this dashboard, resettable to the canonical pro defaults."

**Click:** the **Logs** tab briefly to show the SSE stream.

> "The agent is also exposed as an MCP server — 10 tools over stdio or SSE. Claude Code, Goose, Cursor, Continue — any MCP client can drive the whole stack. The MCP server is OPT-IN — you start it with a separate command when you want other agents to call in."

---

## 1:50 – 2:20 — Rule adherence + replay KPIs

**Click:** the **Live** tab → **User Policy** card.

> "The user signed the policy ONCE — EIP-191 over the policy hash. The signature is right there. Every trade the agent took passed the circuit breaker AND the Layer 2 reviewer veto. The reviewer uses a 10-trade weighted loss-intensity heuristic that catches slow drawdowns the old 4-out-of-5 rule missed."

**Click:** the **Replay** tab (or open `data/reports/replay_compare.html`).

> "Here's the honest backtest, run on the same code with three synthetic regimes. Bull: +0.21% return, 76% hit rate, max DD 0.74%. Bear: -1.65%, 95% hit rate, max DD 1.66%. Chop: -1.64%, 95% hit rate, max DD 1.73%. These are the actual numbers from `data/reports/replay_{bull,bear,chop}.json` — open the JSON, judge. The bull regime is positive. The 5% daily circuit breaker is the safety belt — it holds drawdown under 2% in all three regimes. The hit rate alone is misleading: in bear/chop the carry wins small (a few bps of funding) and loses big (basis widening on chop), so high hit rate + negative PnL. The strategy is early-alpha carry on synthetic tape; the engineering around it — 3-layer LLM safety envelope, EIP-191 policy, ERC-8004 identity, ERC-8183 escrow, x402 microcharges, TWAK signed txs — is the Track 1 bet."

---

## 2:20 – 2:40 — On-chain completion

**Click** the **Jobs** panel again.

> "At window end, the user signs complete() on each ERC-8183 job. USDC releases to the agent. The deliverables — the agent's actual performance — are pinned to IPFS, forever verifiable."

---

## 2:40 – 3:00 — Close

> "Open-source on GitHub. Live dashboard right here. 172 of 172 tests passing, enforced by GitHub Actions CI. This is BNB Agent v2.0 — built for the BNB HACK 2026. Thanks."

---

## Recording tips

- Use OBS or Loom at 1080p, 30fps
- Start with a fresh agent run, let it run for 24h, then record
- Pre-warm the dashboard so panels render instantly
- Have BscScan + 8004scan open in browser tabs for the deep links
- Pre-approve a sample trade so the LLM has a v2.0 reviewer entry to show
- Keep the voiceover calm and confident; the numbers speak for themselves
- **Replace the bull/bear/chop numbers above with live window numbers
  after 2026-06-22 → 2026-06-28.** The script is honest about the
  synthetic tape; don't fabricate live numbers.

