#!/usr/bin/env bash
# Open a new ERC-8183 evaluation window with 4 jobs (A, B, C, ALL).
set -e
cd "$(dirname "$0")/.."
WINDOW_ID="${1:-window-$(date +%s)}"
python3 -c "
import sys; sys.path.insert(0, '.')
from core.boot import boot
from jobs.open_jobs import open_jobs_for_window
import time
c = boot()
usdc = c['config']['tokens']['USDC']
if isinstance(usdc, dict):
    usdc = usdc['bsc_address']
job_ids = open_jobs_for_window(
    window_id='${WINDOW_ID}',
    policy=c['policy'],
    erc8183=c['erc8183'],
    ipfs=c['ipfs'],
    wallet=c['wallet'],
    usdc_address=usdc,
    budget_per_job_usdc=25,
)
import json
print(json.dumps(job_ids, indent=2))
"
