#!/usr/bin/env bash
# Finalize the current ERC-8183 window: submit all deliverables, generate summary.
set -e
cd "$(dirname "$0")/.."
WINDOW_ID="${1:-current}"
python3 -c "
import sys, json; sys.path.insert(0, '.')
from pathlib import Path
from core.boot import boot
from jobs.finalize_window import finalize_window
c = boot()
# load last window's job_ids
files = sorted(Path('data').glob('jobs-*.json'))
if not files:
    print('no jobs opened yet — run scripts/open_window.sh first')
    sys.exit(1)
last = json.load(open(files[-1]))
job_ids = {int(k) if k.isdigit() else k: int(v) for k, v in last['job_ids'].items()}
# also handle string keys
job_ids = last['job_ids']
summary = finalize_window(
    jobs=job_ids,
    portfolio=c['portfolio'],
    policy=c['policy'],
    ipfs=c['ipfs'],
    erc8183=c['erc8183'],
    window_id='${WINDOW_ID}',
)
print(json.dumps(summary, indent=2, default=str))
"
