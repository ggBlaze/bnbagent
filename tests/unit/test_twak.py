"""TWAK wallet — EIP-191, EIP-712, transaction signing."""
import pytest
from web3 import Web3

from connectors.twak import TWAKWallet
from tests.fixtures.wallets import EVALUATOR_KEY, AGENT_KEY, EVALUATOR_ADDRESS, AGENT_ADDRESS


class TestWallet:
    def test_from_private_key(self):
        w = TWAKWallet.from_private_key(EVALUATOR_KEY)
        assert w.address.lower() == EVALUATOR_ADDRESS.lower()

    def test_from_mnemonic(self):
        mnemonic = "test test test test test test test test test test test junk"
        w = TWAKWallet.from_mnemonic(mnemonic)
        assert w.address.startswith("0x")
        assert len(w.address) == 42

    def test_eip191_sign(self):
        w = TWAKWallet.from_private_key(EVALUATOR_KEY)
        sig = w.sign_message_eip191("hello world")
        assert sig.startswith("0x")
        assert len(sig) == 132
        # recover
        from eth_account import Account
        from eth_account.messages import encode_defunct
        recovered = Account.recover_message(encode_defunct(text="hello world"), signature=sig)
        assert recovered.lower() == EVALUATOR_ADDRESS.lower()

    def test_eip191_sign_bytes(self):
        w = TWAKWallet.from_private_key(EVALUATOR_KEY)
        sig = w.sign_message_eip191("0x" + "ab" * 32)
        assert sig.startswith("0x")
        assert len(sig) == 132

    def test_eip712_sign(self):
        w = TWAKWallet.from_private_key(EVALUATOR_KEY)
        domain = {"name": "Test", "version": "1", "chainId": 56, "verifyingContract": "0x" + "f" * 40}
        types = {"Hello": [{"name": "value", "type": "string"}]}
        sig = w.sign_typed_data(domain, types, {"value": "world"})
        assert sig.startswith("0x")
        assert len(sig) == 132

    def test_sign_transaction(self):
        w = TWAKWallet.from_private_key(AGENT_KEY)
        from web3 import Web3
        # use a properly checksummed recipient address
        recipient = Web3.to_checksum_address("0x" + "d" * 40)
        tx = {
            "to":       recipient,
            "value":    10**18,
            "gas":      21000,
            "gasPrice": 5 * 10**9,
            "nonce":    0,
            "chainId":  56,
        }
        signed = w.sign_transaction(tx, chain_id=56)
        assert signed.tx_hash.startswith("0x")
        assert len(signed.raw_tx) > 0

    def test_sign_transaction_requires_nonce(self):
        w = TWAKWallet.from_private_key(AGENT_KEY)
        with pytest.raises(ValueError):
            w.sign_transaction({"to": "0x" + "d" * 40}, chain_id=56)
