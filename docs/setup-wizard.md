# BNB Agent — Setup Wizard

The dashboard ships with a 5-step wizard that walks a new operator from
"nothing on disk" to a running agent. It is the **first** pane shown on
a fresh install and is also reachable from the **Setup** tab.

## What the wizard does

```
Step 1: Network        pick testnet / mainnet / replay; set RPC URLs
Step 2: Wallet         generate a new wallet OR import an existing private key
Step 3: Sign policy    unlock the wallet and sign config/policy.yaml (EIP-191)
Step 4: Data source    pick CMC Pro / x402 on Base / Binance fallback
                       (CMC Pro key input lives here, alongside the tier choice)
Step 5: Done           summary + link to the Live dashboard
```

Behind the scenes:

| Step | API endpoint | What it writes |
|---|---|---|
| 1 | `POST /api/setup/config`   | `config/config.yaml` |
| 2 | `POST /api/setup/wallet` or `/api/setup/wallet/import` | `~/.twak/wallet.json` (AES-256-GCM, PBKDF2 200k) |
| 3 | `POST /api/setup/sign`     | `config/policy.yaml` (signature + evaluator address) |
| 4 | `POST /api/setup/data-source` | `config/config.yaml` → `data_source.*` |
| 5 | — (summary)                  | none |

After step 4, `/api/setup/checklist` returns `{"complete": true, "missing": []}`
and the **Setup** tab stops showing the "setup required" badge.

## v2.0 extras in the wizard

- **Wallet format** is the same as TWAK's CLI (`npx twak sign message` works
  on the same file). Production hardware-wallet support is one CLI command
  away.
- **Mainnet confirmation** is enforced at the API level: `POST /api/setup/config`
  rejects `mode: mainnet` unless the wallet has been signed and the
  policy has been verified. Switching to mainnet from testnet re-runs
  the policy verification.
- **Persona bootstrap** happens on first boot: `agents/_pro_defaults/*.md`
  are copied to `agents/personas/*.md` if not already present. The
  Setup wizard step 3 displays the current pro personas and lets the
  user skip ahead to Live.

## v2.1.0 — Data source step (Step 4)

The new step lets the operator pick **one** of three data sources for
the agent's market-data calls:

```
┌─ Step 4 of 5 — Data source ────────────────────────────────────┐
│                                                                 │
│   ( ) CoinMarketCap Pro  — CMC_API_KEY env var. Free,           │
│                             key-gated, 333 calls/min.           │
│                                                                 │
│   (•) x402 on Base        — Pay-as-you-go, $0.01 USDC/call.    │
│                             Settles on Base (chain 8453) via    │
│                             native USDC. Daily cap default $10. │
│                                                                 │
│   ( ) Binance public     — Free, anonymous, no key. OHLCV-only. │
│                             Fallback if CMC / x402 is down.     │
│                                                                 │
│   ┌─ x402 funding status ──────────────────────────────────┐   │
│   │  Base USDC balance: 0.00 USDC                          │   │
│   │  Wallet: 0x...dE7                                      │   │
│   │  [ waiting for funding... ]                            │   │
│   └────────────────────────────────────────────────────────┘   │
│                                                                 │
│   Base RPCs:                                                   │
│   [x] https://mainnet.base.org                                 │
│   [x] https://base.publicnode.com                              │
│   [x] https://1rpc.io/base                                     │
│   [+ add RPC]   [remove]                                       │
│                                                                 │
│   Daily cap: [   10.00 ] USDC                                  │
│                                                                 │
│   [Back]                                       [Save & Next →]  │
└─────────────────────────────────────────────────────────────────┘
```

The 3-way radio persists to `config/config.yaml` under `data_source.kind`.
The x402 status box polls `connectors/x402.py::check_balance()` over the
3 default Base RPCs and refreshes every 5s — when the operator sends USDC
to the agent's Base address, the balance updates in real time and the
**Save & Next** button enables. The user can also add or remove Base RPCs
from the list (the default 3 are pre-selected); the list is persisted as
`data_source.base_rpcs` and exposed as the `BASE_RPCS` env var.

If the operator picks **x402 on Base** and hasn't sent any USDC yet, the
wizard stays on Step 4 until either funds arrive or the operator switches
to **Binance** (which is always free, no funding required).

## v2.1.0 — Secret-phrase export button (Step 2)

The wallet step now has an **Export secret phrase** button next to the
generated address. The button opens a modal that:

1. Asks the operator to re-enter the wallet password.
2. Calls `POST /api/wallet/export-mnemonic` with the password.
3. Displays the BIP-39 mnemonic **once**, in a copyable text area.

The mnemonic is required to recover the wallet in Trust Wallet, MetaMask,
or any other BIP-39-compatible tool. After the modal is closed, the
phrase is removed from the DOM and is no longer reachable from the UI.

Mitigations (see also [`docs/SECURITY.md`](SECURITY.md#secret-phrase-export-endpoint-v210)):

- The endpoint refuses to operate without a non-empty password.
- The mnemonic is never logged, persisted to disk, or returned in any
  response other than the dedicated `export-mnemonic` endpoint.
- The endpoint is rate-limited at 1 request / minute / IP.
- The endpoint is only reachable from the dashboard's Wallet step inside
  the Setup wizard, which itself requires the wizard to be unlocked.

## Security model

- The private key **never leaves the host process**. The dashboard receives
  only the wallet **address** (0x…) and the keystore **path**; the
  encrypted blob lives at `~/.twak/wallet.json` with `chmod 600`.
- The password is sent over the loopback HTTP connection to the dashboard
  backend. For production, run the dashboard behind a reverse proxy with
  TLS (Caddy, nginx) and bind it to `127.0.0.1`.
- The wallet format is the same as Trust Wallet's Agent Kit (TWAK), so the
  CLI fallback path (`npx twak sign message`) works on the same file.
- The LLM agent team does NOT participate in the Setup wizard. Setup
  is a human-only flow — wallet creation, password entry, and policy
  signing are all user actions.

## Production wallet import

The wizard's "Import existing private key" path is the recommended way to
migrate a pre-existing BSC wallet. The key is encrypted immediately on
receipt and never logged or echoed back to the browser.

For hardware-wallet support (Ledger / Trezor), use the TWAK CLI directly:

```bash
npx twak init --chain bsc --ledger --password-env TWAK_PWD
export TWAK_KEYSTORE=~/.twak/wallet.json
export TWAK_PWD=...
python -m policy.policy_sign
bash bnbagent
```

## Reset

The **Reset Everything** button on the final step wipes:

- `config/config.yaml`
- `config/policy.yaml`
- `~/.twak/wallet.json`
- `~/.bnbagent/setup.json`

It does NOT wipe the personas or the LLM-decisions log. Use it before
donating the box to a new operator or before re-running the wizard with
a different chain.

## Persona management (advanced)

After completing the wizard, the user can:

- **View the active persona** from the Chat pane → "view persona" modal.
  Shows the current `.md` body + sha256 + a "diverged from pro" badge.
- **Edit** the persona body in a textarea and "save" → writes to
  `agents/personas/<name>.md`. The agent picks it up on the next heartbeat.
- **Reset to pro** → copies `agents/_pro_defaults/<name>.md` back over the
  live copy. The diverged badge disappears.

See [`PERSONAS.md`](PERSONAS.md) for details.
