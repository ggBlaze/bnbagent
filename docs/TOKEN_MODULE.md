# BNB Agent — Token Module

A first-class dashboard tab (not a Skill). Deploys ERC-20 / BEP-20 / OpenZeppelin
tokens on BSC, with optional single-file HTML landing-page generation.

## Why a tab, not a Skill

A Skill is a hot-toggled module that reacts to events. Token deploys are
heavy, configurable, and the user wants a UI for them. The Token Module
is its own pane in the dashboard with a dedicated config panel and a
deploy flow that includes a mainnet confirmation modal.

It's also exposed via the **MCP server** so other agents can call our
agent to deploy tokens programmatically. The chat can route "create a
token called X with symbol Y and supply 1B" into a TokenModule call.

## Config (`agents/token_module.yaml`)

```yaml
network: testnet               # testnet | mainnet
protocol: erc20_minimal        # erc20_minimal | bep20 | openzeppelin
default_supply: "1000000000"  # 1B
default_decimals: 18
create_website: true
website_theme: |
  Futuristic dark DeFi landing page with hero, features, roadmap, socials.
  Single-file HTML/JS, no external deps, inline CSS. Mobile-first.
  BNB yellow (#F0B90B) on near-black (#0B0E11).
```

Edit from the dashboard's Tokens pane (the form re-reads on load) or
directly in the YAML.

## Deploy flow

```
POST /api/tokens/deploy
{
  "name": "Mooncoin", "symbol": "MOON", "supply": 1_000_000,
  "decimals": 18, "network": "testnet", "confirm_mainnet": false
}
```

1. **Validate** — name ≤ 64 chars, symbol 3-8 chars, supply > 0, decimals 0-18.
2. **x402-pay CMC** for token metadata enrichment (via `cmc.call("GET", "/v1/cryptocurrency/info", ...)`).
   First call triggers 402; the agent signs EIP-3009 and retries.
   Logged to the x402 microcharge ledger.
3. **Build init code** — `runtime_bytecode + abi_encode((name, symbol, decimals, totalSupply*10**decimals, deployer))`.
4. **TWAK-sign** the contract-creation tx: `{"to": None, "data": "0x" + init_code.hex(), "nonce": ..., "chainId": 56|97, "gas": 1_500_000}`.
5. **Broadcast** via `BSCClient.broadcast(signed)`. In testnet mode, returns
   a deterministic `contract_address` (RIPEM-160 of keccak(sender || nonce)).
6. **Pin metadata** to IPFS via `ipfs.add_json(meta + contract_address)`.
7. **Generate website** (if `create_website: true`):
   - Prompt the chat LLM with the website_theme + the new token's name/symbol
   - LLM returns `{"html": "<!doctype html>..."}`
   - Server-side sanitization strips `eval`, `Function(...)`, `document.write`,
     external `<script src=...>` tags, and inline event handlers
   - If LLM fails, a hard-coded fallback HTML page is generated

## Mainnet safety

Mainnet deploys require **both** layers of confirmation:

1. **API-level**: `confirm_mainnet: true` in the body. Without it: `400`.
2. **UI-level**: the dashboard shows a `<dialog>` that requires the user
   to type the token name in full. If the typed value doesn't match
   the token name, the deploy is aborted.

The MCP tool `bnbagent_deploy_token` also requires `confirm_mainnet: true`.

## Contest window lock (v2.1.6)

The BNB HACK 2026 contest rules forbid token launches between
2026-06-03 and 2026-07-06. The Token Module enforces this in code
via `TokenModule.is_deploy_unlocked()`:

| Gate | Condition | Behavior |
|---|---|---|
| Date lock | `now < 2026-07-07 00:00 UTC` | Always locked. `create_token()` raises `PermissionError`; dashboard route returns HTTP 423 (Locked) with `error: "token_deploy_locked"`. |
| Env opt-in | `now >= 2026-07-07 00:00 UTC` AND `BNBAGENT_ALLOW_TOKEN_DEPLOY=true` | Unlocked. Deploys proceed. |
| Default after window | `now >= 2026-07-07 00:00 UTC` AND env unset | **Still locked.** Belt-and-suspenders: a misconfigured prod env can't accidentally start launching tokens the moment the clock crosses midnight. |

**To enable real deploys after 2026-07-07 UTC**, the operator must:
1. SSH into the host
2. Set `BNBAGENT_ALLOW_TOKEN_DEPLOY=true` in `.env` (or Coolify env UI)
3. Restart the service
4. Log in as admin
5. Confirm mainnet via the dashboard modal (or `confirm_mainnet: true` in API)

The dashboard UI shows a clear banner when the lock is on, naming the
env flag to flip. The pure-logic test in `tests/unit/test_token_lock.py`
covers the boundary cases (off-by-one at midnight, env truthy/falsy
values, `PermissionError` on lock, success path with full network stub).

## Protocols

| Protocol | Description | Bytecode size |
|---|---|---|
| `erc20_minimal` | Hand-rolled minimal ERC-20 (name, symbol, decimals, totalSupply, balanceOf, transfer, approve, transferFrom, mint). | ~5KB |
| `bep20` | Alias for `erc20_minimal` (BSC is EVM-compatible). | ~5KB |
| `openzeppelin` | OpenZeppelin audited ERC-20 (heavier, with permit, snapshots, etc). | ~8KB |

The minimal ERC-20 is hand-rolled in pure Python via ABI encoding — no
`solc` dependency. The OpenZeppelin path precomputes a known-good
bytecode blob under `data/` (if not present, falls back to the minimal).

## TokenDeployResult

```python
@dataclass
class TokenDeployResult:
    contract_address: str
    tx_hash: str
    deployer: str
    name: str
    symbol: str
    decimals: int
    total_supply: int
    ipfs_metadata_cid: str | None
    explorer_url: str
    website_html: str | None = None
    network: str = "testnet"
    protocol: str = "erc20_minimal"
```

Returned by `TokenModule.create_token()`. The dashboard renders a result
card with: contract address, tx hash, deployer, supply, decimals, network,
IPFS CID, "View on BscScan" button, and (if `create_website`) a
"Download website.html" button that creates a blob URL.

## Security

- The website HTML is **sanitized server-side** before being returned to
  the dashboard:
  - External `<script src=...>` tags stripped
  - Inline event handlers (`onclick=`, `onload=`, etc.) stripped
  - `eval(...)`, `Function(...)`, `document.write(...)` calls replaced
    with `/* removed */`
- The LLM is instructed to produce self-contained HTML, but the
  sanitizer is the last line of defense.
- Mainnet deploys are irreversible. The user must type the token name
  in the dashboard modal AND the API must receive `confirm_mainnet: true`.
- All deploys are signed with TWAK; the same audit trail as every
  other tx in the agent.

## Tests

`tests/unit/test_token_module.py` — 14 tests:

- `test_create_token_returns_valid_address` — full happy path
- `test_bytecode_under_8kb` — init code is well under the BSC contract-size limit
- `test_symbol_length_validated` — 3-8 chars
- `test_mainnet_requires_explicit_network` — invalid network rejected
- `test_supply_must_be_positive`
- `test_sanitize_website_strips_eval` / `_strips_external_script_src` / `_strips_event_handlers`
- `test_sanitize_website_empty_returns_empty`
- `test_fallback_website_has_no_external_resources` — when LLM is down
- `test_explorer_url_mainnet_vs_testnet`
- `test_update_config_merges` / `_rejects_unknown_keys`
- `test_create_token_emits_explorer_url`
