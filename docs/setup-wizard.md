# BNB Agent — Setup Wizard

The dashboard ships with a 4-step wizard that walks a new operator from
"nothing on disk" to a running agent. It is the **first** pane shown on
a fresh install and is also reachable from the **Setup** tab.

## What the wizard does

```
Step 1: Network        pick testnet / mainnet / replay; set RPC URLs; CMC key
Step 2: Wallet         generate a new wallet OR import an existing private key
Step 3: Sign policy    unlock the wallet and sign config/policy.yaml (EIP-191)
Step 4: Done           summary + link to the Live dashboard
```

Behind the scenes:

| Step | API endpoint | What it writes |
|---|---|---|
| 1 | `POST /api/setup/config`   | `config/config.yaml` |
| 2 | `POST /api/setup/wallet` or `/api/setup/wallet/import` | `~/.twak/wallet.json` (AES-256-GCM, PBKDF2 200k) |
| 3 | `POST /api/setup/sign`     | `config/policy.yaml` (signature + evaluator address) |

After step 3, `/api/setup/checklist` returns `{"complete": true, "missing": []}`
and the **Setup** tab stops showing the "setup required" badge.

## Security model

- The private key **never leaves the host process**. The dashboard receives
  only the wallet **address** (0x…) and the keystore **path**; the
  encrypted blob lives at `~/.twak/wallet.json` with `chmod 600`.
- The password is sent over the loopback HTTP connection to the dashboard
  backend. For production, run the dashboard behind a reverse proxy with
  TLS (Caddy, nginx) and bind it to `127.0.0.1`.
- The wallet format is the same as Trust Wallet's Agent Kit (TWAK), so the
  CLI fallback path (`npx twak sign message`) works on the same file.

## Production wallet import

The wizard's "Import existing private key" path is the recommended way to
migrate a pre-existing BSC wallet. The key is encrypted immediately on
receipt and never logged or echoed back to the browser.

For hardware-wallet support (Ledger / Trezor), use the TWAK CLI directly:

```bash
npx twak init --chain bsc --password-env TWAK_PWD
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

Use it before donating the box to a new operator or before re-running the
wizard with a different chain.
