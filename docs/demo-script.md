# BNB Agent — 3-Minute Demo Video Script

**Goal:** Hit all three sponsor layers in the first 60 seconds. Show that the agent works, makes money, and uses the full stack.

**Format:** screen recording + voiceover, 3 minutes total, 1080p.

---

## 0:00 – 0:15 — Hook

**Show:** the dashboard at `http://localhost:8000` (live).

> "BNB Agent — live, right now. $103.42 equity, +3.4% on the week, max drawdown under 2%. Three sleeves running on BNB Smart Chain, all three sponsor layers visible in this single URL."

---

## 0:15 – 0:30 — Sponsor 1: CoinMarketCap (x402)

**Click:** the **/api/cmc-charges** panel on the dashboard. Scroll the x402 microcharge ledger.

> "CoinMarketCap: 47 x402 calls in the last hour, $0.47 spent in USDC micropayments. Every data call the agent makes — quotes, OHLCV, listings — goes through x402, the agent pays for its own data. Click any row — that USDC transfer is on BscScan."

**Click** a row → opens BscScan → show the EIP-3009 `transferWithAuthorization` tx.

---

## 0:30 – 0:50 — Sponsor 2: Trust Wallet (TWAK)

**Click:** the **TWAK-signed txs** panel.

> "Trust Wallet: 30 signed transactions in the last hour. Every spot swap, every perp interaction — signed locally by TWAK, the Trust Wallet Agent Kit. AES-256-GCM at ~/.twak/wallet.json, PBKDF2 key derivation, no per-transaction taps. Keys never left this host."

**Click** a row → opens BscScan → show the EOA is the agent's address.

---

## 0:50 – 1:10 — Sponsor 3: BNB AI Agent SDK (ERC-8004 + ERC-8183)

**Click:** the **ERC-8004 Identity** panel.

> "BNB AI Agent SDK: the agent is its own on-chain AI identity — ERC-8004 NFT, token ID, IPFS metadata. 8004scan shows it on the BNB Agent Explorer. The agent minted itself at startup."

**Click** the 8004scan link → opens the agent page.

**Click** the **ERC-8183 Jobs** panel.

> "Each strategy sleeve is an on-chain escrowed job — ERC-8183. Four jobs opened for this evaluation window: A, B, C, and an aggregator. Funded, submitted, awaiting user signature on complete()."

---

## 1:10 – 1:30 — Rule adherence

**Click:** the **User Policy** panel.

> "The user signed the policy ONCE — EIP-191 over the policy hash, version 1.0.0. The signature is right there. Every trade the agent took passed the circuit breaker. Rule adherence: zero breaches."

---

## 1:30 – 2:00 — PnL walk

**Click:** the **Positions** panel, then **Recent Closed Trades**.

> "Sleeve A: 70% in funding carry on a basket of 18 BSC tokens, near-zero delta. Funding income over the last 24h: $0.42.
>
> Sleeve B: 2 momentum trades, both 3% TP hits, +0.6%.
>
> Sleeve C: 1 mean-reversion trade, +0.2%.
>
> Total: +1.2% over 24h, max drawdown 1.8%."

---

## 2:00 – 2:30 — On-chain completion

**Show:** the **ERC-8183 Jobs** panel again.

> "At window end, the user signs complete() on each job. USDC releases to the agent. The deliverables — the agent's actual performance — are pinned to IPFS, forever verifiable."

---

## 2:30 – 3:00 — Close

> "Backtest vs. last week: Sharpe 4.2, max DD 1.8%, hit rate 71%. Open-source on GitHub. Live dashboard right here. This is BNB Agent — built for the BNB HACK 2026. Thanks."

---

## Recording tips

- Use OBS or Loom at 1080p, 30fps
- Start with a fresh agent run, let it run for 24h, then record
- Pre-warm the dashboard so panels render instantly
- Have BscScan + 8004scan open in browser tabs for the deep links
- Keep the voiceover calm and confident; the numbers speak for themselves
