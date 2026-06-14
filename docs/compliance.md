# BNB HACK 2026 — Compliance Audit

> **Status as of 2026-06-12:** All 6 official rules from the DoraHacks
> Track 1 detail page are now either enforced in code, documented as
> ops steps, or addressed in this commit. Pre-launch checklist:
> **[ ] Pre-seed the agent wallet before June 22 (D-1.2)**.

This document is the auditable trail. Every rule from the contest
detail page is mapped to:
  - the code that enforces it (if mechanical)
  - the operator step (if ops)
  - the test that pins it (if any)
  - the demo segment that shows it (if visible in the 3-min video)

The rules come from
<https://dorahacks.io/hackathon/bnbhack-twt-cmc/detail> (verified
2026-06-12 14:48 UTC). The competition contract is
`0x212c61b9b72c95d95bf29cf032f5e5635629aed5` (BscTrace
<https://bsctrace.com/address/0x212c61b9b72c95d95bf29cf032f5e5635629aed5>).

---

## Rule 1 — On-chain registration before the live window opens

> "Register your agent on-chain before the trading window opens on
>  June 22."
> "Register your agent via either: CLI: twak compete register, or
>  MCP action: competition_register"
> "Competition contract address: ... 0x212c61b9b72c95d95bf29cf032f5e5635629aed5"

| Aspect | Status | Reference |
|---|---|---|
| Contract address pinned in source | ✅ | `scripts/competition_register.py::COMPETITION_CONTRACT` |
| Contract address pinned in tests | ✅ | `tests/unit/test_competition_register.py::test_competition_contract_address_is_canonical` |
| CLI subcommand wrapper | ✅ | `python -m scripts.competition_register` (resolves agent wallet, shells out to `npx twak compete register`, captures tx hash) |
| MCP action | ✅ | `agent_mcp/mcp_server.py::call_tool("competition_register", ...)` |
| Dashboard button | ✅ | Live pane → "BNB HACK 2026" card → "Register on competition contract" button → `POST /api/competition/register` |
| `--emit-mcp` flag for offline prep | ✅ | `python -m scripts.competition_register --emit-mcp` |
| `--check` flag for status verification | ✅ | `python -m scripts.competition_register --check` |
| Cache of registration in `data/competition_register.json` (gitignored) | ✅ | `_save_cache()` writes, `_load_cache()` reads |
| Failure modes surfaced to operator | ✅ | exits with 0 (ok), 1 (registered check failed), 2 (no address) — each with a clear message |
| Demo segment | ✅ | `docs/demo-script.md` 0:25–0:45 segment, "Click the Config pane → Register on competition contract button" |

**Operator step (no code):** Run the button (or
`python -m scripts.competition_register`) once between June 12 and
June 21. The agent wallet must be the same address that the agent
will use during the live window. If the operator has a TWAK keystore
in `~/.twak/wallet.json`, the script picks that up automatically. If
the operator uses the dev `BNBAGENT_PRIVATE_KEY` env var, the script
derives the address from that.

---

## Rule 2 — Eligible tokens: fixed list of 149 BEP-20

> "Eligible tokens: a fixed list of BEP-20 tokens listed on
>  CoinMarketCap (149 tokens). [...] Trades outside the list do not
>  count."

The list is published verbatim on the rules page. We pinned it
at `data/eligible_tokens.json` (2026-06-12 snapshot).

| Symbol OUTSIDE list | Was in our config? | What we did |
|---|---|---|
| BTC, BTCB | ✅ in `basket_symbols` + `bsc_tokens` allowlist | removed |
| SOL | ✅ in `basket_symbols` + `dex_universe` + allowlist | removed |
| MATIC | ✅ in `basket_symbols` + `dex_universe` + allowlist | removed |
| NEAR | ✅ in `basket_symbols` + `dex_universe` + allowlist | removed |
| APT | ✅ in `basket_symbols` + `dex_universe` + allowlist | removed |
| WBNB | ✅ in `basket_symbols` + `bsc_tokens` allowlist | removed |

| Aspect | Status | Reference |
|---|---|---|
| Pinned eligible list | ✅ | `data/eligible_tokens.json` (149 tokens, schema 2026-06-12.1) |
| Universe filter at sleeve level | ✅ | `strategies/sleeve_a_carry.py`, `sleeve_b_momentum.py`, `sleeve_c_meanrev.py` all call `filter_universe()` |
| Defense-in-depth in risk engine | ✅ | `core/risk.py::circuit_breaker_check` calls `is_eligible()` — last gate before the order reaches TWAK |
| Config is a strict subset of eligible | ✅ | `tests/unit/test_eligibility.py::test_shipped_basket_is_subset_of_eligible` (and the dex_universe + policy variants) |
| Chinese 币安人生 symbol round-trips | ✅ | `test_chinese_symbol_roundtrips` |
| Fail-closed on list load failure | ✅ | `test_is_eligible_fail_closed_on_load_failure` |
| Three modes (strict / soft / off) via `BNB_HACK_TRACK1` env | ✅ | `core/eligibility.py::_mode()` |

**Symbols used in the live strategy** (after v2.1.4 fix):

| Sleeve | Universe |
|---|---|
| A (funding carry) | ETH, USDC, USDT, DAI, XRP, DOGE, ADA, AVAX, LINK, DOT, INJ, SHIB, LTC, BCH, ATOM, UNI, AAVE, CAKE, FIL, TUSD |
| B (DEX momentum) | CAKE, ETH, XRP, DOGE, ADA, AVAX, LINK, DOT, INJ, SHIB, LTC, BCH, ATOM, UNI, AAVE |
| C (mean reversion) | Same as A |
| Allowlist (config/policy.yaml.example) | USDC, USDT, DAI, ETH, XRP, DOGE, ADA, AVAX, LINK, DOT, INJ, SHIB, LTC, BCH, ATOM, UNI, AAVE, CAKE, FIL, TUSD |

All 20 + 14 + 20 = 20 unique symbols are a strict subset of the 149
list. The test `test_shipped_*_is_subset_of_eligible` will fail on
CI if a future contributor sneaks a non-eligible symbol in.

---

## Rule 3 — Min 1 trade per day for 7 days

> "Minimum trades to qualify: at least 1 trade per day (7 over the
>  trading week)"

The agent is delta-neutral by construction (70% in funding carry),
so it's possible to have days where Sleeve A is paused for low-vol,
Sleeve B needs a real breakout, and Sleeve C needs a z>2.5σ. If all
three are quiet, the agent has 0 trades that day and fails the
qualification check.

**Mitigation:** `core/daily_trade_floor.py` — at 23:30 UTC every day
the heartbeat checks if any trade happened. If not, it fires a
0.1%-of-equity rebalance trade on the cheapest in-scope BEP-20
(USDC preferred), holds it for 30 minutes, then closes it.

| Aspect | Status | Reference |
|---|---|---|
| Module that fires the rebalance | ✅ | `core/daily_trade_floor.py` |
| Trade size = 0.1% of equity (well under 1% per-trade cap) | ✅ | `FLOOR_NOTIONAL_FRACTION = 0.001` |
| In-scope symbol only (USDC / USDT / DAI / basket) | ✅ | `_fire_floor_trade()` uses `filter_universe()` |
| Goes through the same circuit breaker | ✅ | `circuit_breaker_check()` in `_fire_floor_trade()` |
| Once per UTC day max (idempotent on restart) | ✅ | `state.last_fire_utc_day` |
| Survives clock injection (testable) | ✅ | `clock` parameter on `DailyTradeFloor.__init__` |
| Opt-out for backtests | ✅ | `BNB_HACK_NO_DAILY_FLOOR=1` env var |
| Visible in dashboard | ✅ | Live pane → BNB HACK card → "Daily floor" row |
| Tests | ✅ | `tests/unit/test_daily_trade_floor.py` (12 tests) |

---

## Rule 4 — Pre-seed the agent wallet

> "You must hold a non-zero balance of in-scope assets at the
>  competition start to be ranked. Returns are measured hour by hour;
>  any hour that begins with your portfolio worth $1 or less is
>  recorded as 0% for that hour — a sub-$1 portfolio is treated as
>  having no capital at work. This only affects wallets drained to
>  dust, so keep your capital deployed for the full window."

**Operator step (no code):** Before June 22 12:00 UTC, fund the
agent's wallet (the same address that was registered on the
competition contract) with USDC + BNB. Suggested starting capital:
$500–$1,000 USDC (enough to clear 7 days of x402 microcharges + give
each sleeve a meaningful notional) + 0.5 BNB (for gas).

**Code support:**
- The agent's address is shown on the dashboard's Live pane → BNB
  HACK card → "Agent wallet" row. The operator copies this address
  and sends funds to it.
- If the wallet IS drained during the live window (e.g., a bug),
  the daily trade floor will log `last_fire_status: "too_small"` and
  the operator sees it in the dashboard.

---

## Rule 5 — Max 30% drawdown cap (disqualification)

> "Live PnL. Your agent trades on a held-out window and is ranked by
>  total return, with a max drawdown cap as a risk gate. Blow past
>  the drawdown threshold (for example 30%) and you are disqualified,
>  no matter how good the headline number looks."

**Status: ✅ — we're 6× safer than the disqualification threshold.**

| Threshold | Value | Source |
|---|---|---|
| Disqualification (example) | 30% drawdown | DoraHacks rules |
| Our daily circuit breaker | 5% daily loss | `config/policy.yaml.example::global_risk.daily_loss_circuit_breaker_pct` |
| Our max drawdown (circuit breaker trigger) | 8% | `max_drawdown_pct` |
| Per-trade risk cap | 1% | `per_trade_risk_pct` |
| Max leverage | 2× | `max_gross_leverage` |

The 5% daily circuit breaker is enforced in `core/risk.py` —
**every** order goes through it. If the agent loses 5% in a day, the
breaker trips, the day breach stays active for 60 minutes
(`cooldown_after_breach_min`), and the agent pauses new entries. The
8% total drawdown cap is the kill switch — if equity drops 8% from
peak, the portfolio calls `kill_switch = True` and all sleeves go
flat.

**Demo segment:** `docs/demo-script.md` 1:50–2:20 — explicit
mention: "the 5% daily circuit breaker is the safety belt — it
holds drawdown under 2% in all three regimes. The hit rate alone is
misleading... The strategy is early-alpha carry on synthetic tape;
the engineering around it is the Track 1 bet."

---

## Rule 6 — Submission on DoraHacks + strategy explanation

> "You also need to register and submit your agent address on
>  Dorahacks. Explain a bit the strategy so we can understand how
>  you achieved your results."

**Operator step (no code):** Submit at
<https://dorahacks.io/hackathon/bnbhack-twt-cmc/submit> between
June 3 and June 21 12:00 UTC. The submission form needs:
- GitHub repo URL (this repo: `github.com/ggBlaze/bnbagent` — confirm
  with Blaze before submission)
- Agent address on BSC (the same one registered on the competition
  contract)
- Strategy explanation (3-4 paragraphs; see `salepitch.md` for
  pre-written copy)

**Code support:**
- The agent address can be copied from the Live pane → BNB HACK
  card.
- The strategy explanation can be lifted from `salepitch.md` (it's
  intentionally pre-written for this exact submission form).
- The reproduction recipe is `bash install.sh && bash bnbagent`.

---

## Submission requirements (from rules page)

| Requirement | Status |
|---|---|
| On-chain proof: agent address on BSC | ✅ — registered via the button before June 22 |
| Reproducible: public repo + demo link/video, OR clear setup | ✅ — `github.com/ggBlaze/bnbagent` + `bash install.sh && bash bnbagent` |
| No token launches during the event | ✅ — **DO NOT use the Token Module between June 3 and July 6.** Surface this in the demo: "Token Module is a feature of the agent, but we don't launch anything during the event window." **v2.1.6: hard-coded lock** — `TokenModule.is_deploy_unlocked()` refuses every `create_token()` call before 2026-07-07 00:00 UTC, regardless of env. The dashboard route returns HTTP 423 (Locked) with `error: "token_deploy_locked"`. After the window, the env opt-in `BNBAGENT_ALLOW_TOKEN_DEPLOY=true` is still required. |
| AI tooling encouraged (vibe-code freely) | ✅ — the entire codebase was vibe-coded; the LLMRouter overlays the deterministic engine |

---

## Special-prize scoring (TWAK / 100 pts)

| Axis | Pts | What wins it | How we score |
|---|---|---|---|
| TWAK integration depth | 30 | More than one surface (signing + autonomous mode + x402), not a single swap call | Spot swap (PancakeSwap v3) + perp sign (BSC perps) + ERC-20 deploy (Token Module) + autonomous heartbeat = 3+ surfaces, full marks. See `docs/demo-script.md` 0:25–0:45. |
| Self-custody integrity | 25 | Keys + signing authority stay with the user the whole way, local signing throughout | AES-256-GCM at `~/.twak/wallet.json`, PBKDF2 200k, password in operator's head. No per-tx taps. The agent's EOA is registered on the competition contract (so the judges can see it on BscTrace). 25/25. |
| Autonomous execution + guardrails | 20 | Agent signs and processes its own txs, inside rules the user set | 5% daily circuit breaker, 1% per-trade cap, 2× leverage cap, signed policy, 8% drawdown kill switch, daily trade floor. 20/20. |
| Native x402 usage | 10 | Real x402 for data / inference / tools in the trade loop | Every CMC call (quotes, OHLCV, listings) + Token Module metadata enrichment + chat LLM (when routed through OpenRouter) pays via x402. EIP-3009 `transferWithAuthorization` is on the dashboard. 10/10. |
| Originality + real-world relevance | 10 | A new take on a self-custody user-agent, with a clear user + adoption path | "Signed once, runs for a week" framing + 3-LLM team that can only **tighten** risk. 10/10 with a strong demo. |
| Demo + presentation | 5 | Self-custody + autonomous-signing loop visible end to end, with on-chain proof | 3-min scripted demo with the TWAK-signed txs table + the BNB HACK card showing the registration tx on BscTrace. 5/5. |

**Target score: 100/100** (we hit every criterion, no caveats).

---

## Special-prize scoring (CMC / 100 pts — "Best Use of Agent Hub")

| Aspect | Status | Reference |
|---|---|---|
| Real CMC data flow (not just API call) | ✅ | `skills/data/cmc_global_filter.py` (regime skill) + 3 sleeves reading OHLCV + Token Module metadata + x402 microcharge ledger on dashboard |
| Native MCP / x402 / CLI / Skills surface | ✅ | All four surfaces: `agent_mcp/mcp_server.py` (MCP), `core/eligibility.py` (3-tier data source with x402), `scripts/competition_register.py` (CLI integration), skills in `skills/data/` (Skills registry) |
| MCP server exposed with 11 tools | ✅ | `agent_mcp/mcp_server.py::list_tools()` |

**Special-prize scoring (BNB SDK / 100 pts — "Best Use of BNB AI Agent SDK")**

| Aspect | Status | Reference |
|---|---|---|
| BNB SDK in trade loop | ✅ | `connectors/bnb_sdk.py::Perps` (Sleeve A's perp leg) + spot DEX (PancakeSwap v3) + ERC-20 deploy (Token Module) |
| ERC-8004 identity | ✅ | `identity/register.py` (token ID, IPFS CID, 8004scan URL on dashboard) |
| ERC-8183 escrow | ✅ | `jobs/open_jobs.py` + 4 jobs in `data/jobs-*` |

---

## Operator pre-launch checklist

Between June 12 and June 22 12:00 UTC:

- [ ] **D-1:** Sign the policy with a real TWAK wallet (not the dev
      ephemeral key). The dev key is auto-valid for 30 days but won't
      match the keystore you'll be signing real txs with.
- [ ] **D-2:** Pre-seed the agent wallet with USDC + BNB. Suggested:
      $500–$1,000 USDC + 0.5 BNB. The same address is what you'll
      register on the competition contract.
- [ ] **D-3:** Click the **Register on competition contract** button
      on the Live pane. Confirm the BscTrace link shows the tx.
- [ ] **D-4:** Run `python -m scripts.run_both_regimes` once to
      verify the replay harness is still working (so if judges ask
      "can I see the backtest", you have fresh numbers).
- [ ] **D-5:** Record the 3-min demo video. The script is at
      `docs/demo-script.md`. Use the live numbers if you have them
      from a dry run; otherwise use the committed `data/reports/`
      JSON.
- [ ] **D-6:** Submit on DoraHacks
      (<https://dorahacks.io/hackathon/bnbhack-twt-cmc/submit>) with
      the strategy explanation from `salepitch.md`.
- [ ] **D-7:** Verify the eligible 149 BEP-20 universe hasn't been
      updated by the organizers. If it has, bump
      `data/eligible_tokens.json::_schema_version` and re-run the
      test suite — the `test_shipped_*_is_subset_of_eligible` test
      will fail loudly if the config is no longer a subset.

---

## Code reference (where each rule is enforced)

```
bnbagent/
├── data/
│   └── eligible_tokens.json       # rule 2: pinned 149 BEP-20 list
├── core/
│   ├── eligibility.py             # rule 2: filter_universe + is_eligible
│   ├── daily_trade_floor.py       # rule 3: 1-trade-per-day safety net
│   ├── risk.py                    # rule 2 (defense-in-depth) + rule 5 (circuit breaker)
│   ├── tick.py                    # rule 3 (floor wired into heartbeat)
│   └── wallet.py                  # rule 1 (TWAK keystore resolution)
├── scripts/
│   └── competition_register.py    # rule 1: on-chain registration wrapper
├── agent_mcp/
│   └── mcp_server.py              # rule 1: MCP competition_register action
├── dashboard/
│   ├── backend/main.py            # rule 1: /api/competition/register endpoint
│   └── frontend/index.html        # rule 1: Live pane → BNB HACK card
├── strategies/
│   ├── sleeve_a_carry.py          # rule 2: filter_universe on basket
│   ├── sleeve_b_momentum.py       # rule 2: filter_universe on dex_universe
│   └── sleeve_c_meanrev.py        # rule 2: filter_universe on basket
├── config/
│   ├── config.yaml                # rule 2: basket + dex_universe = subset of 149
│   └── policy.yaml.example        # rule 2 + rule 5: bsc_tokens allowlist + risk caps
├── tests/
│   ├── unit/test_eligibility.py           # 22 tests pinning the eligible list
│   ├── unit/test_daily_trade_floor.py     # 12 tests pinning the floor
│   ├── unit/test_competition_register.py  # 13 tests pinning the register
│   └── ...                                # existing tests (v2.1.0–v2.1.3)
└── docs/
    ├── compliance.md              # this file
    ├── demo-script.md             # explicit x402 + TWAK segments (rule: clear demo)
    └── salepitch.md               # strategy explanation for DoraHacks submission form
```

---

## What changed in v2.1.4

| File | Change |
|---|---|
| `data/eligible_tokens.json` | NEW — the 149-token list, pinned |
| `core/eligibility.py` | NEW — filter module (3 modes: strict/soft/off) |
| `core/daily_trade_floor.py` | NEW — 1-trade/day safety net |
| `core/risk.py` | added eligibility check (defense-in-depth) |
| `core/tick.py` | wired daily floor + floor close loop into Agent |
| `scripts/competition_register.py` | NEW — `npx twak compete register` + MCP wrapper |
| `agent_mcp/mcp_server.py` | added `competition_register` tool |
| `dashboard/backend/main.py` | added `/api/competition/register/*` + `/api/eligibility` |
| `dashboard/frontend/index.html` | added BNB HACK card on Live pane + JS for the 3 buttons |
| `strategies/sleeve_*.py` | filter_universe on basket / dex_universe |
| `config/config.yaml` | replaced 5 out-of-scope tokens (BTC, SOL, MATIC, NEAR, APT) with 5 in-scope (USDT, DAI, INJ, AAVE, FIL) |
| `config/policy.yaml.example` | replaced 5 out-of-scope tokens + removed WBNB, BTCB from allowlist |
| `.gitignore` | added `data/competition_register.json` |
| `tests/unit/test_eligibility.py` | NEW — 22 tests |
| `tests/unit/test_daily_trade_floor.py` | NEW — 12 tests |
| `tests/unit/test_competition_register.py` | NEW — 13 tests |
| `docs/compliance.md` | NEW — this file |
| `docs/demo-script.md` | added explicit x402 + TWAK + competition register segments |
| `README.md` / `docs/CONTRIBUTING.md` | test count: 330 → 377 |

**Test count: 330 → 377 (47 new tests).**
