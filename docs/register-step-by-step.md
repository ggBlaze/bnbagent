# BNB HACK — On-Chain Registration: Step-by-Step

> One tx. One command. ~30 seconds of signing. That's it.

---

## What this does

Sends the agent's wallet address to the competition contract on BSC mainnet.
This puts BNB Agent on the **official participant list** the judges score against.
Without this tx, nothing else matters — no live PnL, no dashboard, no demo video.

**Contract:** `0x212c61b9b72c95d95bf29cf032f5e5635629aed5` (BscScan: https://bsctrace.com/address/0x212c61b9b72c95d95bf29cf032f5e5635629aed5)
**Agent address:** `0x5C0115a4d88287e9A6F1B3F70b4e9A4F5eE2a3Bc`
**TX cost:** < $0.10 on BSC (gas ~21K, gasPrice ~3 gwei)

---

## Option A — CLI (fastest, no dashboard needed)

```bash
cd /home/kai/github/bnbagent

# 1. Verify the agent address is what you expect
python3 scripts/competition_register.py --dry-run
# Expected output:
#   [register] dry run — agent address: 0x5C0115a4d88287e9A6F1B3F70b4e9A4F5eE2a3Bc
#   [register] would call: npx twak compete register --network mainnet --contract 0x212c61b9b72c95d95bf29cf032f5e5635629aed5

# 2. Confirm the TWAK wallet is the right one
npx twak wallet list
# You should see the BNB Agent wallet. If it shows a different address, STOP and check your config.

# 3. Send the tx
python3 scripts/competition_register.py --network mainnet
# This calls: npx twak compete register --network mainnet --contract 0x212c61b9b72c95d95bf29cf032f5e5635629aed5
# TWAK will prompt for confirmation if the UI requires it.
# On success: prints BSC tx hash + bsctrace.com link

# 4. Verify it worked
python3 scripts/competition_register.py --check
# Expected: {"registered": true, "txHash": "0x...", "blockNumber": ..., "address": "0x5C0115..."}
```

---

## Option B — Dashboard button

1. Deploy bnbagent (`bash bnbagent`) on your VPS with `BNBAGENT_AUTH_MODE=password` and your admin password set
2. Open the dashboard → Competition pane
3. Click **"Register Agent"**
4. Confirm the agent address shown (`0x5C0115a4d88287e9A6F1B3F70b4e9A4F5eE2a3Bc`) matches what you expect
5. TWAK will prompt to sign — confirm
6. Button changes to **"Registered ✓"** with the tx hash

---

## What gets written

The script writes `data/competition_register.json` (gitignored) so subsequent boots know the agent is already registered and skip the redundant tx.

```json
{
  "registered": true,
  "address": "0x5C0115a4d88287e9A6F1B3F70b4e9A4F5eE2a3Bc",
  "txHash": "0x...",
  "blockNumber": 12345678,
  "registeredAt": "2026-06-17T..."
}
```

---

## Confirm the agent address is correct (before signing anything)

The agent address comes from the TWAK keystore, which is populated by the Setup wizard when you first run `bash bnbagent`. It is NOT the deployer's wallet — it is the dedicated agent wallet created for this competition.

```bash
# Read the agent address from the signed policy (the most authoritative source)
cd /home/kai/github/bnbagent
python3 -c "
import yaml, json
policy = yaml.safe_load(open('config/policy.yaml'))
print('Policy evaluator:', policy.get('evaluator_address', 'NOT SIGNED YET'))
"

# Read it from the TWAK keystore directly
npx twak wallet list
# The agent wallet address should match what competition_register.py --dry-run prints
```

If these three addresses don't match each other, STOP before running `--network mainnet`:

| Source | Expected value |
|---|---|
| `competition_register.py --dry-run` | `0x5C0115a4d88287e9A6F1B3F70b4e9A4F5eE2a3Bc` |
| TWAK wallet list | should match |
| `config/policy.yaml` evaluator_address | should match |

---

## If something goes wrong

- **Wrong address showing:** Do not run `--network mainnet`. Check your `config/local.yaml` and your TWAK keystore at `~/.twak/wallet.json`.
- **"already registered" error:** The agent is already on the participant list. Run `--check` to confirm and get the tx hash.
- **TX reverted:** Check BscScan for the revert reason. Most likely cause: the contract was updated after the rules were published. If you see this, ping the DoraHacks organizers immediately.
- **Permission denied:** TWAK needs to be able to sign. Make sure `~/.twak/wallet.json` is accessible and not corrupted.

---

## Timing

The registration is valid for the entire competition. Send it once, forget it.
**Deadline to send:** 2026-06-21 12:00 UTC (submission lock). The live PnL window opens 2026-06-22 12:00 UTC regardless.

---

## After registration

```bash
# Verify
python3 scripts/competition_register.py --check

# Commit the fact that registration happened (so the repo reflects reality)
git tag -a v2.1.7 -m "BNB HACK 2026 submission — agent registered on-chain"
git push origin v2.1.7
git push   # push the v2.1.7 tag separately
```
