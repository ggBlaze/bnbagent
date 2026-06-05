#!/usr/bin/env bash
# BNB Agent — Day 1 sanity check. Runs in <1 minute, validates the whole stack.
set -e

cd "$(dirname "$0")/.."

# Ensure Python deps are visible. Works for venv-installed packages
# (PYTHONPATH) and for in-place installs (pip install -e .).
VENV_SITE="$(ls -d /tmp/venv/lib/python*/site-packages 2>/dev/null | head -1)"
[ -n "$VENV_SITE" ] && export PYTHONPATH="$VENV_SITE:${PYTHONPATH}"
# Fall back to in-tree .venv if it exists
[ -d .venv/lib ] && export PYTHONPATH="$(ls -d .venv/lib/python*/site-packages | head -1):${PYTHONPATH}"

echo "============================================================"
echo "BNB Agent — First 5 Commands (Day 1 sanity check)"
echo "============================================================"

# 1) Verify Python
echo
echo "▶ 1. Python"
python3 --version

# 2) Verify deps import
echo
echo "▶ 2. Dependencies"
python3 -c "import web3, eth_account, pydantic, fastapi, yaml, jsonschema, numpy, pandas; print('   OK — all deps import')"

# 3) Generate ephemeral wallet (or use BNBAGENT_PRIVATE_KEY if set)
echo
echo "▶ 3. TWAK wallet"
export BNBAGENT_PRIVATE_KEY="${BNBAGENT_PRIVATE_KEY:-0x$(python3 -c "import secrets; print(secrets.token_hex(32))")}"
python3 -c "
from connectors.twak import TWAKWallet
w = TWAKWallet.from_env()
print(f'   address: {w.address}')
print('   OK — wallet ready')
"

# 4) x402 ping (synthetic — we don't actually hit CMC on Day 1)
echo
echo "▶ 4. x402 payment header"
python3 -c "
from connectors.twak import TWAKWallet
from connectors.x402 import build_x402_payment_sync, PaymentRequirements
w = TWAKWallet.from_env()
req = PaymentRequirements(
    scheme='exact', network='bsc', token='0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d',
    amount=10_000, payTo='0x' + 'f'*40, nonce='day1', expiresAt=9999999999, extra={}
)
hdr = build_x402_payment_sync(w, req, chain_id=56)
print(f'   X-PAYMENT header: {hdr[:64]}...')
print('   OK — x402 payment header built')
"

# 5) ERC-8004 register (testnet stub)
echo
echo "▶ 5. ERC-8004 identity (testnet stub)"
python3 -c "
import sys; sys.path.insert(0, '.')
from connectors.bnb_sdk import BSCClient, ERC8004
from connectors.ipfs import IPFSClient
from connectors.twak import TWAKWallet
from identity.register import register_agent
import yaml
cfg = yaml.safe_load(open('config/config.yaml'))
bsc = BSCClient(cfg['rpcs'], cfg['chain_id'], cfg.get('mode', 'testnet'))
e8 = ERC8004(bsc, '0x' + '80'+'04'+'0'*36)
ipfs = IPFSClient(mode='testnet')
w = TWAKWallet.from_env()
import time
window_id = f'day1-{int(time.time())}'
policy = yaml.safe_load(open('config/policy.yaml'))
policy['evaluator_address'] = '0x' + '0'*40
ident = register_agent(e8, ipfs, w, policy)
print(f'   tokenId: {ident[\"token_id\"]}')
print(f'   cid:     {ident[\"cid\"]}')
print('   OK — identity registered')
"

echo
echo "============================================================"
echo "✓ All 5 sanity checks passed. Stack is alive."
echo "  Next: run 'bash scripts/sign_policy.sh' then 'python3 -m core.main'"
echo "============================================================"
