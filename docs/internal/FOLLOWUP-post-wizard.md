# Post-wizard follow-up work — open after v2.1.7

> **Status (2026-06-17):** F1 / F2 / F3 / F4 all landed locally on
> `main` (commits e160be1, b998381, c76a5cb, fe6d551). +42 new tests,
> all green when each suite is run alone. **Not yet pushed.** F5 is an
> operational reminder.
>
> See the **Post-merge open items** section at the bottom for
> follow-ups surfaced during the work.

This file captures the **remaining infrastructure bugs and
features** that were discovered while bringing up the dashboard +
agent end-to-end. The wizard + wallet + LLM + sign + reset flows
are all fixed and committed; what remains is live-runtime work
that needs careful design or larger refactors.

Each section below is self-contained: context, TDD-style test
plan, acceptance criteria. A new session can pick any item and
work it without re-reading the entire session transcript.

---

## F1. Dashboard / agent process split — sidebar empty state

### Symptom (user screenshot, post-wizard live dashboard)

Right-side **System Status** panel, top-left **Agent Identity**
panel, top-left **User Policy** panel, and the **Total Equity /
Today PnL / Drawdown / Open / Sharpe Live** tiles all show
dashes (`—`). Sleeve Toggles + Sleeve Allocation + per-sleeve
cards DO populate (because they come from `config/policy.yaml`
on disk, not the agent's runtime state).

### Root cause

`bash bnbagent` launches TWO separate Python processes:

```
$ bash bnbagent
  ├─ python -m dashboard.backend.main    # FastAPI on :8000
  └─ python -m core.main                 # trading agent loop
```

`core/main.py` populates a module-level `DASHBOARD_STATE` dict
that `dashboard/backend/main.py:74-76` imports:

```python
try:
    from core.main import DASHBOARD_STATE
except ImportError:
    DASHBOARD_STATE = {}
```

The dashboard's `_state()` returns `DASHBOARD_STATE or {}`.
Because the two processes don't share memory, the dashboard's
`DASHBOARD_STATE` is always empty. Every endpoint that goes
through `_state()` returns empty data:

- `GET /api/stats` → `{}`
- `GET /api/config` → `mode: None, chain_id: None`
- `GET /api/identity` → `{"error": "no identity registered"}`
- Sidebar reads (`sys-mode`, `sys-chain`, `sys-addr`,
  `sys-wallet`, `sys-updated`) all show dashes
- Tiles (equity, pnl, drawdown) all dashes

### Fix design — three options, ordered by effort

**Option A (recommended, ~half day)**: Agent writes its state to
a JSON file on a tick (every ~1s, the same cadence as the WebSocket
push). Dashboard reads the JSON file when its `_state()` is called.

- New file: `data/dashboard_state.json` (already exists in
  `.gitignore` family — confirm)
- Agent side: `core/main.py` already has the loop. Add a write
  step after the portfolio tick. Use atomic `.tmp + rename`.
- Dashboard side: replace `from core.main import DASHBOARD_STATE`
  with `def _state(): return json.load(open(DASHBOARD_STATE_PATH))`
  with a TTL cache (e.g., 1s) so we don't read the file on every
  request.
- Acceptance: `/api/stats` returns a populated dict within 2s of
  agent boot. Sidebar shows live values.
- Trade-off: introduces a disk read on the dashboard hot path,
  but the data is small (~1KB) and JSON load is fast. With
  TTL cache, ~1 disk read per second per dashboard instance.

**Option B (~1 day)**: Unix domain socket. Agent listens on
`/tmp/bnbagent.sock` (or similar). Dashboard connects and reads
JSON-serialized state. Same wire format as Option A but no disk.

- Trade-off: needs connection lifecycle management (what if
  agent restarts? dashboard reconnects? backpressure?).

**Option C (~2 days, the right answer long-term)**: Merge the two
processes. Have `core/main.py` run uvicorn in the same process
on a background asyncio task. Eliminates the entire IPC problem.

- Trade-off: needs a thorough audit of `core/main.py` to make
  sure it doesn't block on sync I/O or hold the GIL during
  portfolio updates.

### TDD plan for Option A

1. **Test**: `tests/unit/test_dashboard_state_file.py`
   - Create a fake state dict, write it to `data/dashboard_state.json`
     via the new helper.
   - Assert the dashboard's `_state()` returns it.
   - Assert TTL cache doesn't read disk more than once per second.
   - Assert atomic-write semantics (no partial reads).

2. **Test**: `tests/integration/test_dashboard_with_agent.py`
   - Spin up the agent in a subprocess or background task.
   - Hit `/api/stats` after a 2s grace period.
   - Assert response is non-empty and contains expected keys
     (`stats`, `config`, `policy`, `components.identity`).

3. **Code**:
   - `core/dashboard_state.py` (new) — `write_state(state: dict)`
     and `read_state() -> dict` with TTL cache.
   - `core/main.py` — call `write_state(DASHBOARD_STATE)` after
     each tick (every 1s).
   - `dashboard/backend/main.py:74-76` — replace the cross-process
     import with a file read.

### Files to touch

- `core/dashboard_state.py` (new)
- `core/main.py`
- `dashboard/backend/main.py` (small — just swap `_state()` impl)
- `tests/unit/test_dashboard_state_file.py` (new)
- `tests/integration/test_dashboard_with_agent.py` (new)

### Risk

- None for Option A — pure read-side change.
- Option B/C need more care (lifecycle management).

---

## F2. Sleeve C mean-rev: string vs float in OHLCV

### Symptom (agent.log)

```
ERROR core.tick: sleeve C tick failed: unsupported operand type(s)
for -: 'str' and 'str'
  File "core/tick.py", line 30, in _run
  File "strategies/sleeve_c_meanrev.py", line 57, in tick
  File "strategies/sleeve_c_meanrev.py", line 80, in _scan_signals
    ret_1h = (quotes[-1]["close"] - quotes[-2]["close"]) / quotes[-2]["close"]
TypeError: unsupported operand type(s) for -: 'str' and 'str'
```

### Root cause

Binance public API `GET /api/v3/klines` returns klines as
**arrays**, not objects. The code expects an array-of-dicts with
`{"close": <float>}` keys but is getting an array like
`[<open_time>, <open>, <high>, <low>, <close>, ...]`. OR the
response was stringified at some point. The `close` value comes
through as a string `'<number>'` instead of `<number>`.

### Where to look

- `connectors/binance.py` (or wherever the OHLCV fetch happens)
  — check how it parses the response.
- `data/hybrid_data_source.py` — if there's a normalization layer,
  check that it casts to float.

### Fix

Cast `close` (and other numeric fields) to `float` at the
data-source boundary, not at the strategy boundary. Single fix,
prevents future regressions.

### TDD plan

1. **Test**: `tests/unit/test_binance_ohlcv_typing.py`
   - Mock Binance `/api/v3/klines` returning a realistic payload.
   - Assert `quotes[i]["close"]` is `float`, not `str`.

2. **Fix**: parse + cast in `connectors/binance.py` (or the
   data-source layer that wraps it).

### Files to touch

- `connectors/binance.py` (or wherever the parse happens)
- `tests/unit/test_binance_ohlcv_typing.py` (new)

---

## F3. Reviewer bad JSON from MiniMax

### Symptom (agent.log)

```
INFO  httpx: POST https://api.minimaxi.chat/v1/chat/completions 200 OK
WARN  agents.reviewer: reviewer[A] bad JSON: Expecting value: line 1 column 1 (char 0)
INFO  strategies.sleeve_a_carry: Sleeve A reviewer veto USDC: ok (conf=0.50)
```

The 200 OK comes back, but the response body isn't parseable as
JSON. The reviewer falls back to heuristic (and apparently
defaults to "ok").

### Likely cause

The reviewer prompt expects JSON. `MiniMax-M3` is a **reasoning
model** (per `agents/base.py:185`: "MiniMax M3 emits a think
block before the answer"). The response likely contains a
`<thinking>...</thinking>` block followed by JSON. The naive
`json.loads(response)` fails because of the think block.

### Where to look

- `agents/reviewer.py` — how it parses the response.
- `agents/base.py:185-210` — the existing think-block handling.
  Likely needs to be applied to the reviewer.

### Fix

Apply the think-block-stripping pattern (already in `agents/base.py`)
to the reviewer response parser. JSON.parse on the cleaned string.

### TDD plan

1. **Test**: `tests/unit/test_reviewer_strips_think_block.py`
   - Mock the LLM client to return a response with `<thinking>...`
     prefix and JSON body.
   - Assert reviewer parses the JSON successfully.

2. **Fix**: in `agents/reviewer.py`, strip `<thinking>...</thinking>`
   before `json.loads`.

### Files to touch

- `agents/reviewer.py` (small)
- `tests/unit/test_reviewer_strips_think_block.py` (new)

---

## F4. x402 cost ceiling — "zero amount in payment requirements"

### Symptom (agent.log)

```
INFO  connectors.x402: x402 pay: scheme=exact network=bsc token= amount=0 payTo= nonce=
WARN  strategies.sleeve_a_carry: cmc quote failed for ETH: zero amount in payment requirements
```

x402 quotes come back with `amount=0`, so the sleeve skips the
trade. The agent is alive but not paying for any data.

### Likely cause

CMC's x402 endpoint returns a payment-requirements payload with
a `maxAmountRequired` or similar field. If the field is missing
or named differently, our parser falls back to 0. Or the
endpoint requires a paid quote (not a free one) and we need to
actually pay.

### Where to look

- `connectors/x402.py` — how it constructs the payment header.
- `connectors/cmc.py` — how the CMC client uses the x402
  responses.

### Fix

Inspect the actual payment-requirements payload from CMC and fix
the parser. If x402 is meant to require payment, the wallet needs
USDC on Base (user has 1.0 USDC, should be enough).

### TDD plan

1. **Test**: `tests/unit/test_x402_payment_requirements.py`
   - Mock the CMC endpoint to return a realistic payment-required
     response.
   - Assert the payment header is constructed correctly and the
     amount is > 0.

2. **Fix**: in `connectors/x402.py`, fix the field name or
   fallback.

### Files to touch

- `connectors/x402.py`
- `connectors/cmc.py` (if the bug is there)
- `tests/unit/test_x402_payment_requirements.py` (new)

---

## F5. Wallet funding reminder (operational, not code)

User wallet `0xed669AE6632be9440cdACBE5ac5181D5BC871CC9` is on
**BSC mainnet** with **0 BNB**. The agent will circuit-breaker
every trade (notional > balance). User needs to fund it.

For x402, wallet has **1.0 USDC on Base** (already funded).
Should be enough for ~140 micro-quotes per day at the $0.007
per-quote rate.

### Action (not a code change)

In the next session, remind the user that:
1. Send some BNB to `0xed669AE6632be9440cdACBE5ac5181D5BC871CC9`
   on **BSC mainnet** (chain id 56) to start trading.
2. ~0.05 BNB (~$30) is enough to satisfy the per-trade notional
   cap ($3.50 in policy) + gas.

---

## How to pick up in a new session

1. Read this file: `docs/internal/FOLLOWUP-post-wizard.md`
2. Pick one item (F1 is highest-leverage; F2/F3 unblock trading
   visibility; F4 unblocks CMC data; F5 is just a reminder).
3. TDD per the per-item plan.
4. Commit per item — small, focused commits.

## Context that won't be in a new session's memory

- All commits from this session (push them first if not already
  pushed): 991c123, ac06e87, 50febf2, c4b5c53, 590e765,
  13056f2, 8017be3, 06d129b, 4d593b5, 9152b42, 657690d,
  ad533a6, 94de513.
- The user's actual wallet address: `0xed669AE6632be9440cdACBE5ac5181D5BC871CC9`.
- x402 setup: API key in `.env`, model = `MiniMax-M3`, base
  address set to the wallet, balance ready.
- Dashboard running on `http://localhost:8000`, agent running as
  a sibling process via `bash bnbagent`.

---

## Post-merge open items (surfaced 2026-06-17 during F1–F4 work)

These are smaller follow-ups discovered while landing the main four.
None are blocking; pick up when convenient.

### P1. Pre-existing test-ordering flake (5 tests)

Running the full mixed suite in one process (`pytest tests`) makes
these 5 tests fail; running each in isolation passes them. Reproducible
on `main` BEFORE the post-wizard fixes landed (confirmed via
`git stash` + re-run). Not caused by F1–F4.

  - `tests/unit/test_advisor.py::test_can_only_tighten`
  - `tests/unit/test_advisor.py::test_cannot_loosen_with_higher_value`
  - `tests/unit/test_advisor.py::test_tighten_sleeve_respects_lower_value_only`
  - `tests/unit/test_advisor.py::test_unknown_key_vetoed`
  - `tests/unit/test_providers.py::test_router_status_with_key`

Likely cause: an integration test sets env / .env / module-level router
state that the advisor + providers tests then pick up. Fix is probably
a fixture that resets `agents.providers._cache` / env between tests.

### P2. x402 nonce uniqueness

`connectors/x402.py:_eip3009_nonce("")` returns `keccak(text="")`
deterministically. The canonical x402 spec doesn't put `nonce` in
PaymentRequirements (it's a property of the EIP-3009 authorization, not
the requirement). If CMC ever sends a challenge without a `nonce` field,
the second paid request in a session would collide and the server
should reject it as "nonce already used".

Fix: generate a per-request random 32-byte nonce in `x402_pay` when
`req.nonce` is empty — `secrets.token_bytes(32)`.

### P3. Apply F3's robust JSON extractor to the advisor

`agents/advisor.py:147` does the same bare `json.loads(raw)` the
reviewer used to do. After F3 (`b998381`) the reviewer survives
unclosed `<think>`, `<thinking>`, fences, and prose — the advisor still
falls into its `parsed_ok=False` branch on any of those.

Fix: pull `_extract_json_object` out of `agents/reviewer.py` into
`agents/base.py` (next to `llm_complete`) and use it from both places.
Add a test alongside `tests/unit/test_reviewer_strips_think_block.py`
that exercises the advisor.

### P4. Lossy serialization of `components` in the IPC snapshot

`core/dashboard_state.py:write_state` uses `default=str` so class
instances (BSCClient, PancakeRouter, etc.) become their `repr()` in
the file. Endpoints that need to call methods on those instances
(e.g. `/api/data-source` calls `data_source.status()`) get a string
where they expect an object — same break as the pre-F1 cross-process
state, just visible now. The sidebar/tiles work; the deeper endpoints
don't.

Fix: have each component expose a `to_dashboard_dict()` method and
have `_publish_dashboard_state()` call those. Or: the dashboard
endpoints that need method calls should ask the agent for that data
over the control IPC (`core/control.py`).
