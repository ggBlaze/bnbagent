#!/usr/bin/env bash
# Sign config/policy.yaml with the wallet in BNBAGENT_PRIVATE_KEY.
set -e
cd "$(dirname "$0")/.."
python3 -m policy.policy_sign --policy config/policy.yaml
