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
