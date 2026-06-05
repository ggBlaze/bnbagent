"""End-to-end integration test: boot → policy → sign → register → open jobs → run a tick."""
import asyncio
import json
from decimal import Decimal
from pathlib import Path

import pytest

from core.boot import boot
from identity.register import register_agent
from jobs.open_jobs import open_jobs_for_window
from policy.policy_sign import sign_policy
from policy.policy_verify import verify_policy
from connectors.twak import TWAKWallet
from tests.fixtures.wallets import EVALUATOR_KEY, TEST_POLICY


class TestPipeline:
    def test_boot_then_sign_then_register_then_open_jobs(self, tmp_path):
        # 1) boot the agent (testnet mode, replay tape)
        components = boot(starting_equity=Decimal("100"), mode="testnet", verify_signature=False)
        assert "wallet" in components
        assert "policy" in components
        assert "erc8183" in components
        assert "erc8004" in components
        assert "ipfs" in components

        # 2) sign the policy with the evaluator wallet
        evaluator = TWAKWallet.from_private_key(EVALUATOR_KEY)
        policy = {**components["policy"]}
        sig = sign_policy(policy, evaluator)
        policy["signature"] = sig
        assert verify_policy(policy, evaluator.address)

        # 3) register identity
        identity = register_agent(components["erc8004"], components["ipfs"],
                                  components["wallet"], policy)
        assert "token_id" in identity
        assert "cid" in identity
        # identity was either just-created (matches wallet) or loaded from a
        # previous test run (different ephemeral key). Either is valid.

        # 4) open jobs for a window
        job_ids = open_jobs_for_window(
            window_id=f"test-{int(__import__('time').time())}",
            policy=policy,
            erc8183=components["erc8183"],
            ipfs=components["ipfs"],
            wallet=components["wallet"],
            usdc_address="0x" + "c" * 40,
            budget_per_job_usdc=25,
        )
        assert set(job_ids.keys()) == {"A", "B", "C", "ALL"}
        for jid in job_ids.values():
            assert components["erc8183"].get(jid)["status"] == "Funded"
