#!/usr/bin/env bash
# Mint ERC-8004 identity NFT and pin metadata to IPFS.
set -e
cd "$(dirname "$0")/.."
python3 -c "
import sys; sys.path.insert(0, '.')
from core.boot import boot
from identity.register import register_agent
c = boot()
register_agent(c['erc8004'], c['ipfs'], c['wallet'], c['policy'])
import json
print(json.dumps(json.load(open('~/.bnbagent/identity.json'.replace('~', '${HOME}'))), indent=2))
"
