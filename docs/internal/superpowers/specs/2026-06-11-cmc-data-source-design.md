# 3-Tier CMC Data-Source Design

| | |
|---|---|
| **Status** | Draft — awaiting user review |
| **Date** | 2026-06-11 |
| **Author** | Claude (for Blaze) |
| **Hackathon** | BNB HACK 2026 (judging 2026-06-22 → 2026-06-28) |
| **Sponsors touched** | CoinMarketCap (data), BNB Chain (Base USDC), Trust Wallet (mnemonic) |
| **Files in scope** | `connectors/cmc.py` (rewrite), `connectors/x402.py` (update), `connectors/binance.py` (new), `connectors/data_source.py` (new), `data/cmc_mock.json` (new), `dashboard/backend/main.py` (additive), `dashboard/frontend/index.html` (additive), `core/boot.py` (small wiring change) |

---

## 1. Context

The BNB Agent currently calls `https://api.coinmarketcap.com/agent-hub/v1/cryptocurrency/quotes/latest` and gets 404 on every call. The repo's CMC integration is structurally wrong on five axes (wrong hostname, wrong chain for x402, wrong retry-header name, Pro API key sent to x402 endpoint, wrong path versions).

A deep-research pass (18 confirmed findings, 7 refuted) found:
- **Pro API** lives at `https://pro-api.coinmarketcap.com`, auth header `X-CMC_PRO_API_KEY`, returns the documented v1 paths (`/v1/cryptocurrency/quotes/latest`, etc.). This is the repo's intent but is never actually invoked.
- **x402** lives at `https://pro-api.coinmarketcap.com/x402/...`, settles on **Base (chain 8453)** with **native USDC at `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`** at $0.01/request. The 402-challenge retry header is **`PAYMENT-SIGNATURE`**, not `X-PAYMENT`. The repo's `connectors/x402.py` hardcodes chain 56 (BSC) and USDC.e — a chain-conflict that breaks every x402 call.
- **There is no x402 OHLCV endpoint** — OHLCV is Pro-API-only. The four documented x402 paths are: `/x402/v3/cryptocurrency/quotes/latest`, `/x402/v3/cryptocurrency/listings/latest`, `/x402/v1/dex/search`, `/x402/v4/dex/pairs/quotes/latest`.

The user wants all three data sources to work end-to-end, with a 3-way radio in the Setup wizard, a "change data source" button in the dashboard, and a "export secret phrase" button next to the wallet address.

## 2. Goals

1. The BNB Agent gets live market data from at least one of: CMC Pro API, CMC x402, or Binance public API.
2. The choice is made explicitly in the wizard (no surprises at runtime).
3. The user can change their choice later from the dashboard without restarting the agent.
4. The user can export the TWAK mnemonic to import into MetaMask.
5. Each tier exposes the same async interface, so the strategies don't change.
6. The dashboard surfaces which tier is currently active (banner).

## 3. Non-goals

- Multi-tenant user system. One operator, one wallet, one data-source choice.
- Supporting the BSC USDC.e path for x402. CMC's facilitator only accepts Base USDC.
- A UI to switch the data source mid-trading-session. The router does support hot-swap, but the wizard + dashboard button are for at-rest changes; the change applies to the next call, not the in-flight one.
- Sub-second tier failover. If a tier fails, the call raises — the sleeves hold and log a warning, just like the current 404 behavior.

---

## 4. Architecture

A new `MarketDataSource` Protocol sits between the strategies and the data sources. The strategies don't change. A `DataSourceRouter` (in `connectors/data_source.py`) holds one active source and delegates calls to it.

```
            ┌────────────────────────────────────────────────┐
strategies │ MarketDataClient (facade, in core/portfolio.py)│
            └──────────────────┬─────────────────────────────┘
                               │ quotes_latest / ohlcv_historical / ...
                               ▼
            ┌────────────────────────────────────────────────┐
            │ DataSourceRouter (connectors/data_source.py)  │
            │  - tier = config["data_source"]["tier"]         │
            │  - delegates to one of:                        │
            │     - CMCProClient    (tier == "cmc_pro")      │
            │     - CMCX402Client   (tier == "x402")          │
            │     - BinanceClient   (tier == "binance")      │
            │     - MockClient      (tier == "mock")         │
            └────────────────────────────────────────────────┘
```

### Interface

```python
class MarketDataSource(Protocol):
    async def quotes_latest(self, symbols: list[str], convert: str = "USD") -> dict: ...
    async def ohlcv_historical(self, symbols: list[str], time_period: str = "hour",
                                count: int = 24, convert: str = "USD") -> dict: ...
    async def cmc_rank_map(self) -> dict[str, int]: ...
    async def global_metrics(self) -> dict: ...
    async def fear_and_greed(self) -> dict: ...
    async def dex_listings(self, limit: int = 100) -> dict: ...
    async def exchange_listings(self, limit: int = 100) -> dict: ...
    @property
    def tier(self) -> str: ...      # "cmc_pro" | "x402" | "binance" | "mock"
    @property
    def status(self) -> dict: ...   # for the dashboard banner
```

### The 4 sources

| Tier | URL pattern | Auth | Cost | Coverage |
|---|---|---|---|---|
| **CMC Pro** | `https://pro-api.coinmarketcap.com/v1/...` | `X-CMC_PRO_API_KEY` header | Monthly subscription | 100% (all 7 calls) |
| **x402** | `https://pro-api.coinmarketcap.com/x402/v3/...` | EIP-3009 over Base USDC | $0.01/call, capped at $10/day | quotes/latest, listings/latest, dex/search, dex/pairs/quotes/latest (NO OHLCV) |
| **Binance** | `https://api.binance.com/api/v3/...` | none | free | quotes (price), OHLCV (klines); CMC-only fields → mock |
| **Mock** | local fixture | none | free | whatever's in `data/cmc_mock.json` |

### Base RPC URLs (for the x402 path)

x402 needs to read the Base USDC balance of the user's wallet (to detect funding). That requires a Base RPC. The BNB Agent ships with **3 default Base RPC URLs** and lets the user add or remove them in the wizard, mirroring the existing BSC RPC UX:

| Default | Source | Notes |
|---|---|---|
| `https://mainnet.base.org` | Base (official) | Public, no API key, occasionally rate-limited under heavy use |
| `https://base.publicnode.com` | PublicNode | Free public endpoint |
| `https://1rpc.io/base` | 1RPC | Free public endpoint |

The list lives in `config/config.yaml` under `data_source.base_rpcs` and is also exposed as the `BASE_RPCS` env var (comma-separated, similar to `BSC_RPCS`). `connectors/x402.py` rotates through the list on connection failure (same pattern as `BSCClient`).

### Hot-swap

`DataSourceRouter.set_source(new_source)` replaces the active source. The new source takes effect on the next call. The dashboard's "Change data source" button calls this after writing the new choice to `config/config.yaml`.

---

## 5. Wizard UI

A new step in the Setup wizard — **Step 3 of 4, "Data source"** — sits between "Network" and "Wallet". (Reordering the steps is a separate concern; if preferred, the data-source step can be inserted as the last step before "Sign Policy" — the spec is agnostic to ordering.)

### Layout

```
┌──────────────────────────────────────────────────────┐
│ Data source                                          │
│ ────────────                                         │
│                                                      │
│  ( ) CoinMarketCap Pro API                           │
│      API key: [____________________]  [Get one]     │
│                                                      │
│  (•) x402 (pay-per-request, USDC on Base)            │
│      Your Base address:                              │
│        0xABC…123        [Copy]                       │
│      Chain: Base (8453)                              │
│      Required: 1.00 USDC (0x8335…2913)               │
│      Base RPC URLs (for USDC balance polling):       │
│        [ https://mainnet.base.org          ] [×]     │
│        [ https://base.publicnode.com       ] [×]     │
│        [ https://1rpc.io/base              ] [×]     │
│        [+ Add RPC URL]                               │
│      Waiting for funding… [polling every 10s]        │
│      Current balance: 0.00 USDC                      │
│                                                      │
│  ( ) Binance public API (no setup needed)            │
│      ⚠ Limited: prices + OHLCV only.                 │
│        CMC-only fields will be mocked.               │
│                                                      │
│  [Back]                                   [Continue] │
└──────────────────────────────────────────────────────┘
```

- Picking **CMC Pro** reveals an API key input + "Get one" link to the CMC plan page.
- Picking **x402** reveals the Base address (derived from the TWAK mnemonic), the Base RPC URLs (3 defaults, add/remove), and a polling indicator. The **Continue** button is disabled until the Base USDC balance is ≥ $0.50. A **Back** button lets the user change their mind and switch to a different tier.
- Picking **Binance** shows a warning + "Continue" is always enabled.

### Secret-phrase export button

In the **Wallet** step (existing), next to the address display, add an **Export** button. Clicking opens a modal:

```
┌──────────────────────────────────────────────────────┐
│ Export secret recovery phrase                         │
│ ────────────────────────────                         │
│ ⚠ Anyone with this phrase can drain your wallet.     │
│   Never share it. Never paste it on a website.        │
│                                                      │
│ ☐ I understand the security implications             │
│                                                      │
│ [Cancel]                                  [Reveal]   │
└──────────────────────────────────────────────────────┘
```

After the user checks the box and clicks Reveal, the 12/24-word phrase is shown with a copy-to-clipboard button. The modal is dismissable; the phrase is **not** cached client-side.

### Dashboard Config pane

Add a "Data source" card:

```
┌──────────────────────────────────────────────────────┐
│ Data source                                          │
│ ────────────                                         │
│ Active: x402                                         │
│ Base address: 0xABC…123                              │
│ USDC balance: $0.00                                  │
│ Today's spend: $0.00 / $10.00                        │
│                                                      │
│ [Change data source]    [Export secret phrase]       │
└──────────────────────────────────────────────────────┘
```

"Change data source" re-opens the wizard step in modal form, with the same radio + the same setup prompts (key input for Pro, funding wait for x402, warning for Binance). Persisting the new choice updates the config and hot-swaps the source.

### Persistent data-source banner

A thin bar at the top of the **Live** pane shows the current tier:

```
[DATA] x402 · Base USDC $0.00 · daily $0.00 / $10.00    [change]
```

When the active tier is **Binance**, the banner includes the word "fallback":

```
[DATA] binance (fallback) · prices + OHLCV only · some metrics mocked    [change]
```

---

## 6. Backend API additions

New endpoints in `dashboard/backend/main.py`. All additive — no existing endpoint changes.

```
GET  /api/data-source                 → { tier, status, daily_spend_usdc,
                                          daily_cap_usdc, base_address,
                                          base_usdc_balance, base_rpcs, ... }
POST /api/data-source/select          → { tier: "cmc_pro" | "x402" | "binance" }
                                          persists to config["data_source"]["tier"]
POST /api/data-source/cmc-key         → { api_key: "..." }
                                          sets config["data_source"]["cmc_api_key"]
                                          (encrypted at rest in config.yaml)
POST /api/data-source/base-rpcs       → { base_rpcs: [url1, url2, ...] }
                                          persists the list; validates each URL
                                          is a valid http(s) URL; min 1, max 5
GET  /api/data-source/x402-balance    → { address, balance_usdc, ready }
                                          polls Base USDC balance via the
                                          configured base_rpcs (rotates on fail)
POST /api/wallet/export-mnemonic      → body: { password: "..." }
                                          returns { mnemonic: "word1 word2 ..." }
                                          one-time per request, never logged
```

The `data-source/select` endpoint validates the choice and refuses to switch to a tier whose prerequisites aren't met (e.g. switching to `x402` without a Base USDC balance). The `export-mnemonic` endpoint requires the password in the same request, returns the mnemonic once, and the password is not retained.

---

## 7. Connectors refactor

### `connectors/cmc.py` (rewrite of the existing 167-line module)

- The old `CMCClient` class is split into two:
  - `CMCProClient` — uses Pro API URLs (`/v1/...`), sends `X-CMC_PRO_API_KEY`.
  - `CMCX402Client` — uses x402 URLs (`/x402/v3/...`), signs via `connectors/x402.x402_pay`, sends `PAYMENT-SIGNATURE` on retry.
- Both expose the `MarketDataSource` Protocol.
- The old `from_config()` factory is replaced by `DataSourceRouter.from_config()`.

### `connectors/x402.py` (EIP-3009 signing layer, update)

- Add `chain_id: int = 8453` and `token_address: str = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"` as parameters.
- Add a list of `base_rpcs: list[str]` (default 3 URLs) for balance polling; rotate on connection failure (same pattern as `BSCClient`).
- The old BSC USDC.e values (`chain_id=56`, `0x55d398326f99059fF775485246999027B3197955`) are kept as deprecated fallbacks so existing tests don't break.
- Header names: `PAYMENT-REQUIRED` on the 402 challenge, `PAYMENT-SIGNATURE` on retry.
- Add `check_balance(w3, token, holder) -> Decimal` helper for the wizard's polling.
- Add a per-call ledger (similar to what the old `CMCClient` had) so the dashboard's x402 microcharge pane works.

### `connectors/binance.py` (new, ~80 lines)

Thin async httpx wrapper around `https://api.binance.com/api/v3/`:
- `quotes_latest` → `GET /api/v3/ticker/price?symbols=[...]`.
- `ohlcv_historical` → `GET /api/v3/klines?symbol=...&interval=...&limit=...`.
- Other methods raise `NotImplementedError` — the router catches that and falls back to the mock for that one call.

### `connectors/data_source.py` (new, ~150 lines)

- `class MarketDataSource(Protocol)` — the interface above.
- `class DataSourceRouter` — holds one `MarketDataSource`, delegates all 7 calls.
  - `set_source(new_source)` for hot-swap.
  - `from_config(config, wallet)` factory that picks the right source.
  - For CMC-only calls (e.g. `fear_and_greed`) on the Binance source, fall back to the mock.
- `class MockClient` — loads `data/cmc_mock.json` and returns from it.

### `data/cmc_mock.json` (new, ~3 KB)

Hardcoded values for the CMC-only fields:
- `fear_and_greed`: an integer 0–100 with a label.
- `global_metrics`: market cap, 24h volume, BTC dominance, etc.
- `dex_listings`: top 5 BSC DEXes.
- `exchange_listings`: top 5 CEXes.
- `cmc_rank_map`: ~50 tokens with `{symbol: rank}` for the top-50 CMC list.

### `core/boot.py` (small wiring change)

Replace:
```python
components["cmc"] = CMCClient.from_config(...)
```
with:
```python
components["data_source"] = DataSourceRouter.from_config(config, wallet)
```

The rest of `core/` and `strategies/` is untouched.

### `dashboard/backend/main.py` (additive)

Adds the 5 new endpoints above. Touches nothing else.

### `dashboard/frontend/index.html` (additive)

Adds:
- A new wizard step (between "Network" and "Wallet" by default, ordering is flexible).
- A modal for the secret-phrase export.
- A "Data source" card in the Config pane.
- A persistent "Data source" banner at the top of the Live pane.
- A "Change data source" modal that re-opens the wizard step.

---

## 8. Commit plan (step-by-step, each with verification)

Each commit is independent — stopping after any one of them leaves the agent bootable. The default tier at every step is `mock`, so unconfigured agents keep working.

### Commit 1 — `feat(data-source): add MarketDataSource Protocol + 4 concrete clients`

**Files:** `connectors/data_source.py` (new), `connectors/binance.py` (new), `data/cmc_mock.json` (new), `connectors/cmc.py` (rewrite), `tests/unit/test_data_source.py` (new), `tests/unit/test_cmc.py` (extend).

**Touches heavy?** `connectors/cmc.py` is in the "security boundary" set, but the rewrite is structural, not behavioral — the new code only changes the URL prefix and the auth header. The signing path (EIP-3009 in `x402.py`) is unchanged in this commit.

**Verify:** `pytest tests/unit/test_data_source.py tests/unit/test_cmc.py -v` — all sources return their respective shapes (Binance returns arrays, CMC returns objects, mock returns from JSON). The agent's `/api/stats` endpoint still returns 200 with `tier: "mock"`.

### Commit 2 — `feat(x402): port to Base chain + PAYMENT-SIGNATURE header + Base RPCs`

**Files:** `connectors/x402.py` (update), `config/config.yaml` (add `data_source.base_rpcs` with 3 defaults), `.env.example` (add `BASE_RPCS`), `tests/unit/test_x402.py` (update).

**Touches heavy?** `connectors/x402.py` is in the "security boundary" set. This commit updates the default `chain_id` and `token_address`, renames the header, and adds the `base_rpcs` list with rotation. Existing tests that asserted the old BSC USDC.e values get a `chain_id=56` parameter (backward-compat) so they continue to pass.

**Verify:** `pytest tests/unit/test_x402.py -v` — all unit tests pass; a manual call to `https://pro-api.coinmarketcap.com/x402/v3/cryptocurrency/quotes/latest?symbol=ETH` returns a 402 with a `PAYMENT-REQUIRED` header that `decode_payment_requirements()` parses correctly; the `check_balance()` helper returns a real USDC balance when pointed at one of the default Base RPCs.

### Commit 3 — `feat(router): DataSourceRouter + wiring into boot`

**Files:** `core/boot.py` (small change), `tests/unit/test_boot.py` (new or extend).

**Touches heavy?** `core/boot.py` is in the "heavy" set, but the change is one line — replace `CMCClient.from_config(...)` with `DataSourceRouter.from_config(...)`. The downstream `components` dict shape changes from `{"cmc": CMCClient}` to `{"data_source": DataSourceRouter}`, but no other code reads `components["cmc"]` directly — the strategies call the methods on whatever client they receive, which is now the router.

**Verify:** `pytest -q` — all 226+ tests pass. `bash bnbagent` boots, `/api/data-source` returns `{tier: "mock", status: {...}}`.

### Commit 4 — `feat(dashboard): data-source step in Setup wizard + Config pane button`

**Files:** `dashboard/backend/main.py` (additive), `dashboard/frontend/index.html` (additive), `tests/integration/test_dashboard.py` (extend).

**Touches heavy?** `dashboard/backend/main.py` and `dashboard/frontend/index.html` are in the "heavy" set, but the change is purely additive (new endpoints, new wizard step, new modal, new card, new banner). No existing code is modified.

**Verify:** manually walk through the wizard in a browser — pick each of the 3 tiers, see the expected behavior (key input, funding wait, warning banner). The "Change data source" button in the Config pane re-opens the wizard step. `/api/data-source` reflects the current tier.

### Commit 5 — `feat(dashboard): export-mnemonic button + Base USDC balance polling`

**Files:** `dashboard/backend/main.py` (additive), `dashboard/frontend/index.html` (additive), `tests/integration/test_dashboard.py` (extend).

**Touches heavy?** Same — additive only. The `export-mnemonic` endpoint is security-sensitive: it requires the password in the request body, returns the mnemonic once, never logs or persists the password.

**Verify:**
- The export button shows the phrase after a password is provided; copy-to-clipboard works; the phrase is not in the DOM after the modal closes.
- The x402 balance polling updates the displayed balance every ~10s when the wizard is on the x402 step.
- The Continue button on the x402 step enables when the balance reaches ≥ $0.50.
- The dashboard's "Data source" card shows the Base USDC balance and the daily spend.

---

## 9. Risks

1. **Base USDC balance polling needs a Base RPC.** Three defaults are shipped (`https://mainnet.base.org`, `https://base.publicnode.com`, `https://1rpc.io/base`) and the user can add/remove in the wizard. The list is exposed as `BASE_RPCS` (comma-separated env var) and stored in `config/config.yaml` under `data_source.base_rpcs`. `connectors/x402.py` rotates through the list on connection failure (same pattern as `BSCClient`).
2. **TWAK doesn't currently support non-BSC chains.** `connectors/twak.py` signs for chain_id 56 only. The signer itself is chain-agnostic (secp256k1 is universal), so adding a `chain_id` parameter is a small additive change — but the mnemonic-derivation path also needs to support the Ethereum standard (`m/44'/60'/0'/0/0`). The current TWAK uses a single-key derivation; we'll add a method `derive_address_at(path: str) -> str` that returns the address without exposing the key.
3. **The `bnbagent-sdk` is imported in `connectors/bnb_sdk.py` for BSC operations.** It won't be used for the Base balance read — we'll use `web3` directly with the public Base RPC. Keeps the dependency surface narrow.
4. **Existing strategy tests assume CMC-shaped data (objects with `data` key).** Binance returns arrays. Some of the strategy tests will need updates to handle Binance-shaped data. Done as part of Commit 1.
5. **`x402` tier cannot serve OHLCV.** The agent's Sleeve A uses `ohlcv_historical`; if the user picks x402, that call has no equivalent. The agent will fall back to the mock fixture (a small static OHLCV array in `cmc_mock.json`) and Sleeve A will see "stale" data. This is a real degradation — the user should know. The dashboard's data-source banner will say "x402 · OHLCV mocked" when this is the case.
6. **The export-mnemonic endpoint is the most security-sensitive endpoint added.** Mitigations: password required in request body, never logged, returned once and forgotten, rate-limited at 1 per minute per IP, audited by the existing security-review doc.

---

## 11. Documentation sync (final commit)

The repo's README and several docs reference the *old* x402-on-BSC flow. After the 5 implementation commits land, a **Commit 6** brings the docs back in sync. Touch list:

| Doc | Change |
|---|---|
| `README.md` | • §5 "Sponsor integration" — replace "Settlement is on BNB Chain via USDC.transferWithAuthorization" with "Settlement is on Base (chain 8453) via USDC.transferWithAuthorization". <br> • §6 "Quick start" — mention the new Data-source wizard step. <br> • §12 "Environment variables" — add `BASE_RPCS` to the table. |
| `docs/x402.md` | Major rewrite: new URLs (`pro-api.coinmarketcap.com/x402/...`), new headers (`PAYMENT-REQUIRED` / `PAYMENT-SIGNATURE`), new chain (Base, 8453), new USDC contract (`0x833589…2913`), new Base RPC config with 3 defaults, the 402-challenge sequence diagram, the daily-spend cap. |
| `docs/setup-wizard.md` | Add the new "Data source" step in the 4-step walkthrough, with the 3-way radio mockup and the secret-phrase export button. |
| `docs/operations.md` | Mention the persistent data-source banner in the Live pane and the "Change data source" card in the Config pane. |
| `docs/onchain.md` | Update the x402 section to reflect Base settlement and the new contract. |
| `docs/CHANGELOG.md` | Add `v2.1.0 — 3-tier CMC data source (CMC Pro / x402 on Base / Binance fallback), export-mnemonic button, Base RPC config, daily-spend cap`. |
| `salepitch.md` | Update the "what we built" section to mention the 3-tier data source + export-mnemonic. |
| `docs/CHANGELOG.md` and `docs/SECURITY.md` | Note the new `export-mnemonic` endpoint under security review. |

**Why a separate commit:** doc-only changes are easy to review in one pass; interleaving them with code changes makes the diff harder to follow. The 5 code commits reference the relevant doc snippets inline (e.g. Commit 2 updates the docstring of `connectors/x402.py` to point to Base); Commit 6 is the doc-pass that catches everything else.

---

## 12. Versioning

This is a substantive architectural change (new Protocol, new router, new wizard step, new endpoints, new docs). It warrants a **minor version bump** to `v2.1.0`. The CHANGELOG entry will be:

```
v2.1.0 — 3-tier CMC data source
  ADDED: 3-tier data-source selection (CMC Pro / x402 on Base / Binance
         fallback) via the Setup wizard + a 'Change data source' button
         in the Config pane.
  ADDED: Persistent data-source banner in the Live pane.
  ADDED: Secret-phrase export button in the Wallet step + the
         /api/wallet/export-mnemonic endpoint.
  ADDED: Base RPC config (3 defaults, add/remove, rotation) in the
         x402 wizard step.
  CHANGED: x402 now settles on Base (chain 8453) with native USDC at
           0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913. The retry
           header is now PAYMENT-SIGNATURE (was X-PAYMENT).
  FIXED:   The 404 on https://api.coinmarketcap.com/agent-hub — the
           correct x402 base is https://pro-api.coinmarketcap.com/x402.
  CHANGED: The CMC integration is now a MarketDataSource Protocol with
           4 concrete clients (CMCProClient, CMCX402Client, BinanceClient,
           MockClient) behind a DataSourceRouter.
```

---

## 13. References

- Deep-research synthesis (internal): "How should the BNB Agent's CoinMarketCap (CMC) integration work?" — 18 confirmed findings, 7 refuted.
- [https://coinmarketcap.com/api/documentation/ai-agent-hub/x402](https://coinmarketcap.com/api/documentation/ai-agent-hub/x402)
- [https://pro.coinmarketcap.com/api/v1](https://pro.coinmarketcap.com/api/v1) (informational; the actual API base is `pro-api.coinmarketcap.com`)
- [https://pro.coinmarketcap.com/llms-full.txt](https://pro.coinmarketcap.com/llms-full.txt) (machine-readable API reference)
- [https://coinmarketcap.com/api/documentation/guides/authentication](https://coinmarketcap.com/api/documentation/guides/authentication)
- [https://github.com/coinbase/x402/blob/main/specs/schemes/exact/scheme_exact_evm.md](https://github.com/coinbase/x402/blob/main/specs/schemes/exact/scheme_exact_evm.md) (x402 exact-EVM scheme spec)
- [https://eips.ethereum.org/EIPS/eip-3009](https://eips.ethereum.org/EIPS/eip-3009) (EIP-3009 `transferWithAuthorization`)
- [https://docs.base.org/docs/network-information](https://docs.base.org/docs/network-information) (Base mainnet RPC endpoints)
- [https://publicnode.com/](https://publicnode.com/) (PublicNode free public RPC)
- [https://1rpc.io/](https://1rpc.io/) (1RPC free public RPC)
- Existing repo: `connectors/cmc.py` (current broken integration), `connectors/x402.py` (current BSC-targeted x402), `dashboard/backend/main.py` (current 40+ endpoint layout), `core/boot.py` (current boot flow).
