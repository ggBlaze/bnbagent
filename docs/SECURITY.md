# BNB Agent — Security Model

## v2.0.8 security review (June 2026)

A focused security review was performed in v2.0.8 covering the wallet
keystore, the BSC RPC layer, the gas-pricing path, the CMC data
dependency, the MCP server surface, and the vol-pause logic. The
**full review (private)** lives outside this repo at
`~/.openclaw/workspace-hax/Projects/audits/bnbagent-security-review-2026-06-08.md`
(only the operator can read it; not committed to the public repo).

Summary of the v2.0.8 fixes shipped:

| ID | Severity | Summary | Status |
|----|----------|---------|--------|
| H-1 | HIGH    | Gate `BNBAGENT_PRIVATE_KEY` env var behind explicit opt-in | ✅ Fixed (v2.0.8-H1) |
| H-2 | HIGH    | Add `pycryptodome>=3.18` to deps + hoist AES imports | ✅ Fixed (v2.0.8-H2) |
| H-3 | HIGH    | `resync_nonce` to reconcile local cache from chain | ✅ Fixed (v2.0.8-H3) |
| H-4 | HIGH    | `max_gas_price_gwei` from policy + refuse stuck-tx window | ✅ Fixed (v2.0.8-H4) |
| M-3 | MEDIUM  | MCP SSE binds 127.0.0.1 + optional Bearer auth | ✅ Fixed (v2.0.8-M3) |
| M-4 | MEDIUM  | Vol filter fallback above pause threshold | ✅ Fixed (v2.0.8-M4) |
| M-1, M-2, M-5, M-6, M-7 | MEDIUM | Documented as post-hackathon hardening in the private review |
| L-1 to L-6 | LOW | Minor; deferred |
| I-1 to I-6 | INFO | Positive observations (risk engine order, code-enforced safety envelopes, etc.) |

All 4 HIGH findings were production-only footguns — none were
exploitable in testnet mode. The 2 MEDIUM findings shipped in v2.0.8
are the ones with hackathon-demo-visible behavior (M-3 = the MCP
server's bind default, M-4 = a CMC blip not force-closing the carry
book). The remaining MEDIUMs are documented in the private review as
post-hackathon hardening work.

## Threat model

| Asset | Adversary | Mitigation |
|---|---|---|
| Agent's private key | Disk theft, browser XSS, log exfiltration | AES-256-GCM at `~/.twak/wallet.json`, never in browser, never in logs |
| User-signed Policy | Replay, downgrade, forgery | EIP-191 over `keccak256(canonical_json(body))`; verified at every boot |
| x402 microcharge flow | Replay, double-spend, MITM | EIP-3009 `validBefore` nonce, base64 over HTTPS, BSC finality <200ms |
| ERC-8183 job escrow | Provider rug-pull, evaluator rug-pull | Funds locked on-chain; user (evaluator) holds the `complete()` key |
| LLM agent team | Prompt injection, hostile responses | Hard-coded safety envelopes in code; never delegated to the LLM |
| Token deploy (mainnet) | Typos, accidental real-BNB spend | `confirm_mainnet: true` + user types token name in dashboard modal |
| MCP exposure | Untrusted remote agent | Confirm guards on token deploy; otherwise read-only |

---

## Private key management

### Generation

```python
from connectors.keystore import create_keystore
create_keystore("password-≥8-chars")  # writes ~/.twak/wallet.json with chmod 600
```

### Encryption

- **Cipher**: AES-256-GCM
- **IV**: 12 bytes (random per encrypt)
- **Key**: 32 bytes derived from password via PBKDF2-HMAC-SHA256, 200,000 iterations, 16-byte random salt
- **Auth tag**: 16 bytes appended to ciphertext
- **Format**: matches the Trust Wallet Agent Kit keystore (interoperable with `npx twak`)

### Storage

- Path: `~/.twak/wallet.json` (configurable via `TWAK_KEYSTORE`)
- Mode: `chmod 600` (set automatically on create)
- Backup: **strongly recommended**. The keystore is the only copy.

### Decryption

The wallet's private key is decrypted **only inside the host process** — inside `TWAKWallet.sign_message_eip191()`, `TWAKWallet.sign_transaction()`, and `TWAKWallet.sign_typed_data()`. It is never:

- sent to the browser (the dashboard only sees the address)
- written to a log file
- included in any HTTP response
- passed to a third-party SDK that doesn't need it (e.g. we sign with `eth_account`, not the upstream `twak` subprocess)

### Production recommendations

1. Use a **dedicated operator key** — separate from any wallet that holds your main funds.
2. **Rotate the password** every 90 days. The keystore format supports re-encryption without changing the key.
3. For high-value mainnet runs, use a **hardware wallet** (Ledger / Trezor) — TWAK supports `ledger:` URIs natively. The Setup wizard does not yet expose this; do it via `npx twak init --ledger`.
4. **Never** check `config/policy.yaml`, `~/.twak/wallet.json`, or any file containing the signature into a public repo.
5. Use a **reverse proxy with TLS** (Caddy, nginx) in front of the dashboard. The dashboard's auth posture depends on the mode:
   - `BNBAGENT_AUTH_MODE=disabled` (default) — dashboard is open. Local dev only. **Do not expose a disabled-mode instance to the public internet.**
   - `BNBAGENT_AUTH_MODE=password` — JWT-style cookie gate with 2 roles (judge read-mostly, admin full). Suitable for VPS with an operator doing maintenance.
   - `BNBAGENT_AUTH_MODE=readonly` — no password, every mutation returns 403. **This is the recommended mode for the DoraHacks public URL.** Judges see everything, can't break anything. Defense in depth: even forging an admin cookie doesn't help (the cookie is bypassed entirely in readonly mode).

---

## User Policy integrity

The User Policy (`config/policy.yaml`) is the only file the user signs.
The signature is:

```
digest = keccak256(canonical_json(policy_without_signature))
sig    = eip191_personal_sign(digest, wallet)
```

`canonical_json` is `json.dumps(d, sort_keys=True, separators=(",", ":"))`. The agent re-computes the digest at every boot and refuses to start if the recovered signer doesn't match `evaluator_address`.

**Verifying manually:**

```bash
python -m policy.policy_verify   # → VERIFIED or INVALID
```

**Bumping the policy version** (e.g. to relax a constraint):

```python
from policy.policy_version import bump_version
bump_version("config/policy.yaml", level="minor")  # 1.0.0 → 1.1.0
# archives the old version to config/policy-archive/
```

The user must re-sign after every bump.

---

## LLM agent team — safety envelopes

The 3 LLM layers are written to be **fail-safe**. Every safety property is
enforced in code (`_apply`, `review_trade`, `_tool_recommend`), not by
prompt engineering.

### Layer 1 — StrategyAdvisor (can only TIGHTEN)

Every action in the LLM's response is parsed and validated against a
strict schema. The "can only tighten" rule is enforced in `advisor._apply`:

| LLM says | What happens |
|---|---|
| `tighten_risk(key, new)` where `new < current` | applied via `core.control.write_control` |
| `tighten_risk(key, new)` where `new >= current` | vetoed, logged, no write |
| `loosen_risk(key, new)` | vetoed, logged, no write |
| `disable_sleeve(name)` | applied |
| `enable_sleeve(name)` (not in current spec) | rejected as `unsupported_type` |
| Anything else | rejected as `unsupported_type` or `unknown_key` |

Even a fully malicious LLM response cannot raise a cap. The test
`test_cannot_loosen_with_higher_value` locks this invariant.

### Layer 2 — TradeReviewer (can only VETO)

Hard-coded post-LLM guardrails in `reviewer.review`:

1. `confidence < 0.70` → veto (`source="low_confidence"`)
2. Sleeve drawdown > 50% of policy cap → veto
3. EWMA win-rate on this symbol < 20% → veto
4. Post-loss cooldown active → veto
5. Last 5 trades on this symbol: ≥ 4 losses → veto
6. LLM timeout (>0.5s) → fall back to heuristic-only

The test `test_heuristic_overrides_llm` locks #2-5.

### Layer 3 — ChatAgent (can only RECOMMEND)

The chat's `recommend_risk_change` tool returns a recommendation and a
UI prompt to the Setup wizard. It **never** writes to the policy or the
control file. The test `test_recommend_risk_change_does_not_write` locks
this.

The chat's `sign_new_policy` tool returns a UI prompt only. The user must
go to Setup → re-sign the policy with their wallet password.

---

## x402 integrity

- EIP-3009 `transferWithAuthorization` has a `validBefore` timestamp and a `nonce`. Replay is prevented by the nonce; expiry by `validBefore`.
- The agent signs with `validAfter = now - 60s` and `validBefore = req.expiresAt` (from the server's 402 response).
- The USDC contract is the native Circle-issued USDC on **Base** (chain 8453) at `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` — supports EIP-3009 natively. (v2.0 used the BSC USDC; v2.1.0 moved to Base per CMC's x402 spec.)
- The retry header is `PAYMENT-SIGNATURE` (v2.0 used `X-PAYMENT`).
- Daily spend is capped by `policy.fees.x402_max_usdc_per_day` (default $10). The agent refuses any CMC call that would exceed the cap.

## Secret-phrase export endpoint (v2.1.0, hardened in v2.1.6)

The `/api/wallet/export-mnemonic` endpoint returns the TWAK mnemonic if
the correct password is provided in the request body. Mitigations:

  - **v2.1.6 env gate (default OFF in production):** the endpoint
    returns 403 unless `BNBAGENT_ALLOW_WALLET_EXPORT=true` is set in
    the server env. A judge who somehow learns the admin cookie still
    cannot dump the seed phrase unless they ALSO have SSH access to
    the host, edit the env, and restart the service. 4 factors.
  - **v2.1.5 admin gate:** the route is admin-only (requires the
    admin cookie, which is HMAC-SHA256 signed with `BNBAGENT_AUTH_SECRET`).
  - Password is required in the request body; the endpoint refuses to
    operate with a missing or empty password.
  - The password is never logged, persisted, or returned in any other
    response. The mnemonic is returned once and forgotten.
  - The endpoint is rate-limited at 1 request/minute per IP.
  - The endpoint is only reachable through the dashboard's Wallet
    wizard step, which requires the Setup wizard to be complete.
  - The phrase is removed from the DOM when the modal is closed.

## Wallet import endpoint (hardened in v2.1.6)

The `/api/setup/wallet/import` endpoint accepts a private key in the
request body and writes it to the encrypted keystore. v2.1.6 added:

  - **Env gate (default OFF in production):** returns 403 unless
    `BNBAGENT_ALLOW_WALLET_IMPORT=true` is set in the server env. A
    judge with the admin cookie still cannot replace the operator's
    wallet with their own (which would let them drain funds or
    register a fake ERC-8004 identity). Same 4-factor protection as
    the export endpoint.

## Token Module contest lock (v2.1.6)

The BNB HACK 2026 contest rules forbid token launches between
2026-06-03 and 2026-07-06. The Token Module enforces this in code
via `TokenModule.is_deploy_unlocked()`:

  - **Date gate (always on):** before 2026-07-07 00:00 UTC, every
    `create_token()` call raises `PermissionError` regardless of any
    env var. The dashboard route returns HTTP 423 (Locked) with a
    JSON body that names the env flag needed to opt in.
  - **Env gate (default OFF):** after the date passes, the module is
    STILL locked unless `BNBAGENT_ALLOW_TOKEN_DEPLOY=true` is set.
    This is belt-and-suspenders: a misconfigured prod env can't
    accidentally start launching tokens the moment the clock crosses
    midnight on July 7.

The pure-logic test in `tests/unit/test_token_lock.py` covers the
boundary cases (off-by-one at midnight, env truthy/falsy values,
permission error on lock).

---

## ERC-8183 integrity

Each evaluation window opens 4 jobs (A/B/C/ALL) with the user as evaluator. The state machine:

```
Open ──fund()──> Funded ──submit(proof)──> Submitted ──complete()──> Completed
                  │                          │
                  └──claimRefund()──> Refunded └──reject()──> Rejected
```

The user holds the `complete()` key — they can reject any job and the USDC stays in escrow. The provider (agent) cannot self-release.

---

## Skill side effects

`cmc_global_filter` is the **only Skill that writes** to the control file. Its writes are tagged `_source: "skill:cmc_global_filter"` so the dashboard's Control Log distinguishes them from operator and advisor edits. To disable this Skill: `POST /api/skills/cmc_global_filter/disable`.

All other Skills (telegram, farcaster, webhook, x_sentiment, glassnode) are **pure consumers** — they read state and emit external side effects (Telegram DM, Farcaster post, webhook POST). They cannot write to the agent's policy, control file, or portfolio.

---

## MCP exposure

The MCP server (`agent_mcp/mcp_server.py`) reads `core.main.DASHBOARD_STATE` — it has **no filesystem or network access of its own** beyond the standard MCP stdio/SSE transport. The 11 tools it exposes are:

- 7 read-only tools (pnl, positions, trades, policy, skills)
- 1 recommendation-only tool (`bnbagent_recommend_risk_change`)
- 1 deploy tool (`bnbagent_deploy_token` — mainnet still requires `confirm_mainnet: true`)
- 1 chat tool (`bnbagent_chat` — read-only grounded; tools it can call are limited)
- 2 skill toggle tools (`enable_skill` / `disable_skill`)

The MCP server is **in the same trust boundary as the agent**. Don't expose it to the public internet without auth.

---

## Replay safety

`bash bnbagent --replay` runs the strategies against a synthetic 7-day tape. The `replay` mode in `BSCClient.broadcast` returns deterministic stubs — no real txs are signed, no real network calls are made (other than to LLM providers if configured).

The replay harness reads only from the in-memory tape + the boot's deterministic stubs. It cannot leak real keys or real portfolio data.

---

## Logging

The agent logs structured JSON to `logs/agent.log` (path configurable).
Logged fields: `event`, `ts`, `sleeve`, `symbol`, `notional`, `pnl_usdc`,
`reason`, `tx_hash`, `decision`, `confidence`, etc.

**Never** logged:

- The private key (only the address is logged)
- The wallet password (never passed to the logger)
- API keys (only the env var *names* and the LLM provider responses are logged)

The dashboard's Logs pane streams the log file via SSE. The file is `chmod 644` by default — for production, chmod 600 (the log doesn't contain secrets, but the address is sensitive).

---

## What we deliberately did NOT do

- **No remote key storage.** The private key is on disk, encrypted, on the operator's host. No cloud KMS, no HSM (other than via the TWAK CLI's `--ledger` flag for hardware wallets).
- **No LLM can sign or send a tx directly.** The wallet is owned by the agent process; the LLM cannot exfiltrate it. The LLM can only recommend, veto, or chat.
- **No background daemons.** The agent runs in the foreground of a single terminal. No cron jobs, no systemd, no docker-compose auto-restart that could re-sign with a stale policy.
- **No "AI decides whether to bypass the risk engine."** The LLM can recommend changes to the policy. The risk engine is the only enforcer.

These are deliberate constraints. If you fork this and remove them, you do so at your own risk.
