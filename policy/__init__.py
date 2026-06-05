"""BNB Agent — policy signing + verification.

A User Policy is a signed YAML file. The signature is an EIP-191 personal_sign
over keccak256(canonical_json(policy_without_signature)). The user signs ONCE
per policy version. The agent rejects any order that would violate the policy.
"""
