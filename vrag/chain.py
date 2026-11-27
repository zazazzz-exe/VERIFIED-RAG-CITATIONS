"""web3 connection, deploy, publish, and read calls for CorpusRegistry.

Glassnet accepts only legacy (type-0) transactions, so every transaction is built by
`send_legacy_tx` with an explicit `gasPrice` and never with EIP-1559 fields.
"""

from __future__ import annotations

import json
from typing import Any

from eth_account import Account
from web3 import Web3

from vrag.config import ROOT, Config
from vrag.store import Corpus

ARTIFACT_PATH = (
    ROOT / "blockchain" / "artifacts" / "contracts" / "CorpusRegistry.sol" / "CorpusRegistry.json"
)
GAS_MARGIN = 1.2
FAUCET_URL = "https://faucet.glassnet.prismo.network"


class WrongChainError(RuntimeError):
    """The RPC reports a different chain ID than the configured network expects."""


class ChainError(RuntimeError):
    """Any other blockchain problem (not configured, unreachable, failed transaction)."""


def connect(config: Config) -> Web3:
    """Connect to `RPC_URL` and check that its chain ID matches `NETWORK`."""
    w3 = Web3(Web3.HTTPProvider(config.rpc_url, request_kwargs={"timeout": 30}))
    try:
        actual = w3.eth.chain_id
    except Exception as exc:
        raise ChainError(f"cannot reach the RPC at {config.rpc_url}: {exc}") from exc
    expected = config.expected_chain_id
    if actual != expected:
        raise WrongChainError(
            f"NETWORK={config.network} expects chain ID {expected}, "
            f"but {config.rpc_url} reports chain ID {actual}"
        )
    return w3


def load_artifact() -> dict[str, Any]:
    """ABI and bytecode from the Hardhat production build."""
    if not ARTIFACT_PATH.exists():
        raise ChainError(
            f"contract artifact not found at {ARTIFACT_PATH}. "
            r"Run `npx hardhat build --build-profile production` in blockchain\ first."
        )
    return json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))


def _account(config: Config) -> Account:
    if not config.private_key:
        raise ChainError("PRIVATE_KEY is not set in .env (needed to deploy and publish)")
    try:
        return Account.from_key(config.private_key)
    except Exception:
        # Never include the key itself in the message.
        raise ChainError("PRIVATE_KEY in .env is not a valid private key") from None


def _registry(config: Config, w3: Web3):
    if not config.registry_address:
        raise ChainError("REGISTRY_ADDRESS is not set in .env (run `python -m vrag deploy` first)")
    if not Web3.is_address(config.registry_address):
        raise ChainError(f"REGISTRY_ADDRESS is not a valid address: {config.registry_address!r}")
    return w3.eth.contract(
        address=Web3.to_checksum_address(config.registry_address), abi=load_artifact()["abi"]
    )


def send_legacy_tx(w3: Web3, call: Any, private_key: str) -> Any:
    """Sign and send one legacy (type-0) transaction and return its receipt.

    `call` is a contract function call or constructor call (anything with `estimate_gas`
    and `build_transaction`). Used by both `deploy` and `publish`.
    """
    sender = Account.from_key(private_key).address
    params = {
        "from": sender,
        "type": 0,
        "gasPrice": w3.eth.gas_price,
        "nonce": w3.eth.get_transaction_count(sender, "latest"),
        "chainId": w3.eth.chain_id,
    }
    try:
        # Pin to "latest": Glassnet (cdk-erigon) can report a zero balance for "pending".
        gas = int(call.estimate_gas(params, block_identifier="latest") * GAS_MARGIN)
        tx = call.build_transaction({**params, "gas": gas})
        # eth_account signs a legacy transaction when `type` is absent; guard against
        # EIP-1559 fields ever slipping in (Glassnet rejects them).
        tx.pop("type", None)
        if {"maxFeePerGas", "maxPriorityFeePerGas"} & tx.keys():
            raise ChainError("refusing to send a non-legacy transaction")
        signed = Account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    except Exception as exc:
        hint = f" Get test USDC from {FAUCET_URL}." if "fund" in str(exc).lower() else ""
        raise ChainError(f"transaction failed: {exc}.{hint}") from exc
    if receipt["status"] != 1:
        raise ChainError(f"transaction {tx_hash.hex()} reverted")
    return receipt


def deploy(config: Config) -> str:
    """Deploy CorpusRegistry owned by the deployer. Returns the contract address."""
    w3 = connect(config)
    account = _account(config)
    artifact = load_artifact()
    factory = w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"])
    receipt = send_legacy_tx(w3, factory.constructor(account.address), config.private_key)
    return receipt["contractAddress"]


def publish(corpus: Corpus, config: Config, w3: Web3 | None = None) -> int:
    """Publish the corpus root. Returns the corpus ID from the CorpusPublished event."""
    w3 = w3 or connect(config)
    registry = _registry(config, w3)
    _account(config)
    call = registry.functions.publish(
        corpus.root, corpus.name, corpus.version, corpus.private, len(corpus.passages)
    )
    receipt = send_legacy_tx(w3, call, config.private_key)
    events = registry.events.CorpusPublished().process_receipt(receipt)
    if len(events) != 1:
        raise ChainError("publish transaction did not emit a CorpusPublished event")
    return int(events[0]["args"]["corpusId"])


def verify_passage(
    config: Config,
    corpus_id: int,
    proof: list[bytes],
    doc_id: str,
    page: int,
    index: int,
    content_hash: bytes,
    w3: Web3 | None = None,
) -> bool:
    """Read-only `eth_call` to `verifyPassage`. Raises ChainError if the chain is unreachable."""
    w3 = w3 or connect(config)
    registry = _registry(config, w3)
    try:
        return bool(
            registry.functions.verifyPassage(
                corpus_id, proof, doc_id, page, index, content_hash
            ).call()
        )
    except Exception as exc:
        raise ChainError(f"verifyPassage call failed: {exc}") from exc


def get_corpus(config: Config, corpus_id: int, w3: Web3 | None = None) -> dict[str, Any]:
    """Corpus record from the registry."""
    w3 = w3 or connect(config)
    registry = _registry(config, w3)
    try:
        root, name, version, is_private, count, published_at = registry.functions.getCorpus(
            corpus_id
        ).call()
    except Exception as exc:
        raise ChainError(f"getCorpus({corpus_id}) failed: {exc}") from exc
    return {
        "corpusId": corpus_id,
        "root": root,
        "name": name,
        "version": version,
        "isPrivate": is_private,
        "passageCount": count,
        "publishedAt": published_at,
    }


def address_url(config: Config, address: str) -> str:
    return f"{config.explorer_url.rstrip('/')}/address/{address}"


def corpus_count(config: Config, w3: Web3 | None = None) -> int:
    w3 = w3 or connect(config)
    try:
        return int(_registry(config, w3).functions.corpusCount().call())
    except Exception as exc:
        raise ChainError(f"corpusCount() failed: {exc}") from exc
