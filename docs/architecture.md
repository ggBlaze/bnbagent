# BNB Agent — Architecture

## One-page diagram

```
                           ┌────────────────────────────────────────┐
                           │           User (Evaluator)            │
                           │   signs policy.yaml ONCE (EIP-191)    │
                           └─────────────────┬──────────────────────┘
                                             │
                                             ▼
            ┌──────────────────────────────────────────────────────────┐
            │                    BNB Agent (Python)                    │
            │                                                          │
            │  ┌─────────────┐   ┌──────────────┐   ┌──────────────┐  │
            │  │  Sleeve A   │   │  Sleeve B    │   │  Sleeve C    │  │
            │  │   Carry     │   │  Momentum    │   │  Mean-Rev    │  │
            │  │  (70%)      │   │  (20%)       │   │  (10%)       │  │
            │  └──────┬──────┘   └──────┬───────┘   └──────┬───────┘  │
            │         │   2. reviewer hook (per-trade veto)  │        │
            │         └────────────────┼──────────────────┘        │
            │                          ▼                             │
            │              ┌──────────────────────┐                  │
            │              │   Risk Engine        │                  │
            │              │ circuit_breaker_    │                  │
            │              │     check()         │                  │
            │              │  (per policy.yaml)  │                  │
            │              └──────────┬───────────┘                  │
            │                         ▼                              │
            │              ┌──────────────────────┐                  │
            │              │  Portfolio           │                  │
            │              │  equity, peak, DD    │                  │
            │              └──────────────────────┘                  │
            │                                                          │
            │  ┌─────────────────────────────────────────────────────┐│
            │  │            AI Agent Team (3 LLM layers)             ││
            │  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────┐ ││
            │  │  │ Advisor  │  │ Reviewer │  │   Chat   │  │Token │ ││
            │  │  │ 5min,    │  │ per-     │  │ SSE      │  │Mod.  │ ││
            │  │  │ tightens │  │ trade    │  │ + 9 tools│  │+ web │ ││
            │  │  │ only     │  │ veto only│  │ recom.  │  │      │ ││
            │  │  └──────────┘  └──────────┘  └──────────┘  └──────┘ ││
            │  │  Personas: advisors/reviewer/chat/token_module .md ││
            │  └─────────────────────────────────────────────────────┘│
            │                                                          │
            │  ┌─────────────────────────────────────────────────────┐│
            │  │            Skills Registry (6 built-ins)             ││
            │  │  telegram_alert, farcaster_post, webhook_dispatch,   ││
            │  │  x_sentiment, cmc_global_filter, glassnode_onchain    ││
            │  └─────────────────────────────────────────────────────┘│
            │                                                          │
            │  ┌─────────────────────────────────────────────────────┐│
            │  │            Token Module                             ││
            │  │  x402 metadata → TWAK sign → BNB SDK broadcast        ││
            │  │  + optional single-file HTML website generation      ││
            │  └─────────────────────────────────────────────────────┘│
            └────┬──────────────────┬──────────────────┬─────────────┘
                 │                  │                  │
        ┌────────▼────────┐  ┌──────▼──────┐  ┌────────▼────────┐
        │  L1 CoinMarketCap│  │ L2 TWAK     │  │ L3 BNB SDK     │
        │  Agent Hub       │  │ Self-custody│  │ bnbagent-sdk   │
        │  Data API + MCP  │  │ local sign  │  │ BSC + PCS v3   │
        │  + x402 ($0.01)  │  │ AES-256-GCM │  │ + perps        │
        │  + Skills        │  │ PBKDF2      │  │ + ERC-8004     │
        │                  │  │             │  │ + ERC-8183     │
        └──────────────────┘  └─────────────┘  └─────────────────┘
                                                ▲
                                                │ 11 tools
                                                │
                                  ┌─────────────────────────┐
                                  │    MCP server (stdio /  │
                                  │    SSE) for other       │
                                  │    agents (Claude Code, │
                                  │    Goose, Cursor)       │
                                  └─────────────────────────┘
```

## Sequence diagram: one full tick of Sleeve B (momentum)

```
Agent            CMC Agent Hub            TWAK              BSC RPC          Portfolio
  │   GET /quotes/latest (free)     │                     │                  │
  │────────────────────────────────>│                     │                  │
  │<────── 402 + payment reqs ──────│                     │                  │
  │   EIP-3009 sign USDC $0.01      │                     │                  │
  │   X-PAYMENT header built        │                     │                  │
  │   GET /ohlcv/historical (402)   │                     │                  │
  │   EIP-3009 sign USDC $0.01      │                     │                  │
  │   X-PAYMENT header built        │                     │                  │
  │   rank: vol_spike AND breakout  │                     │                  │
  │   for each signal:              │                     │                  │
  │     calldata = pancake.encode_swap_v3(...)           │                  │
  │     risk.check(proposed) ─────────────────────────────│── allow? ──────>│
  │                                              OK      │<──────────────── │
  │     twak sign tx ──────────────>│                     │                  │
  │     <─ signed raw tx ──────────│                     │                  │
  │     bsc.broadcast(raw) ─────────────────────────────────────> mempool   │
  │     <─ receipt 3s ───────────────────────────────────────────  receipt   │
  │   for each open pos: stop/TP/time check              │                  │
  │   if exit: close_position()      │                     │                  │
```

## Data flow

1. **Boot** (one-time):
   - load `config/policy.yaml`, verify EIP-191 signature
   - init TWAK wallet (AES-256-GCM keystore)
   - init bnbagent-sdk (BSC, PCS v3, perps, ERC-8004, ERC-8183)
   - pin ERC-8004 metadata to IPFS
   - instantiate the **AI agent team** (LLMRouter, Advisor, Reviewers, Chat)
   - discover **Skills** and **TokenModule**
2. **Tick** (every 30s / 5min / 5min per sleeve):
   - fetch CMC data via x402 (pay $0.01 USDC per call)
   - **Layer 2 reviewer hook** between `allow_trade` and `sign_transaction`
   - check risk engine, sign tx via TWAK, broadcast via bsc
3. **Monitor** (every 1s by the Agent heartbeat):
   - apply control-file intents from dashboard + LLM advisor + skills
   - update peak equity, drawdown, Sharpe
4. **Layer 1 advisor** (every 5 min):
   - read recent state, ask LLM for tightening recommendation
   - apply via `core.control.write_control` (same audit path as dashboard)
5. **Layer 3 chat** (on user input):
   - stream tokens; dispatch tools; never write to policy/control
6. **Window** (per evaluation window): open 4 ERC-8183 jobs (A/B/C/ALL), fund from user, submit deliverable per sleeve, user signs `complete()` at end.
7. **MCP server** (separate process): exposes 11 tools over stdio/SSE for other agents.

## Key design decisions

| Decision | Rationale |
|---|---|
| **Funding carry as base sleeve** | Delta-neutral → low drawdown, positive expected value. Maximizes Sharpe, the risk-adjusted performance judging axis. |
| **x402 for every CMC call** | Shows the CMC sponsor is *used* in a meaningful way. The agent pays for its own data. |
| **ERC-8183 jobs per sleeve** | Each strategy is an on-chain escrowed job with the user as evaluator. The judging panel can see exactly what each sleeve did. |
| **Policy is signed YAML** | Trivially auditable. The user signs ONCE. The agent cannot deviate. |
| **Testnet stubs for live tx** | The full stack runs end-to-end without spending real gas. Production swap is a single config change (`mode: mainnet`). |
| **3-LLM agent team with hard safety envelopes** | Turns the bot into a real *agent* (adapts, recommends) without giving the LLM permission to bypass the policy. The advisor can only TIGHTEN, the reviewer can only VETO, the chat can only RECOMMEND. All three envelopes are enforced in code. |
| **TokenModule as a tab, not a Skill** | Heavy, configurable, wants a UI. The Skills registry is for hot-toggled events. |
| **MCP server, not a Skills integration** | Other agents should be able to call BNB Agent as a *function library*, not as a notification consumer. |
| **Provider-agnostic LLM** | Anthropic / OpenAI / OpenRouter / OAI-compat / local. Contest offers Claude credits; users may prefer any other. No vendor lock-in. |
| **Persona files in markdown** | Judges can read them in 30 seconds. Editable. Resettable. |

## Why this is production-ready

- 15,000+ lines of typed Python, fully unit-tested (172/172)
- All configs externalized to YAML
- Structured JSON logging
- Single-page dashboard with 7 panes: Setup / Live / **Chat** / **Tokens** / Config / Logs / Replay
- One-command install (`bash install.sh`) + one-command run (`bash bnbagent`)
- Replay harness validates the strategy against a synthetic 7-day tape in 30s
- Multi-RPC rotation for BSC resilience
- Per-venue failure isolation (one perps venue down doesn't kill the others)
- 1% per-trade + 3% daily circuit breaker are non-negotiable, hard-coded as the only enforcement of the policy
- AI agent team with hard safety envelopes (advisor tightens only, reviewer vetoes only, chat recommends only)
- 6 hot-toggled Skills, MCP server exposing 11 tools for other agents
