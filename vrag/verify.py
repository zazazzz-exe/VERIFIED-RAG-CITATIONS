"""Citation checks: on-chain Merkle proof, quote check, and receipts.

The content hash is always recomputed from the passage text that is shown to the user
(plus the stored salt for private passages). Stored hashes are never trusted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from web3 import Web3

from vrag import chain, merkle
from vrag.chain import ChainError
from vrag.config import Config
from vrag.rag import _BRACKET, Answer, parse_citations
from vrag.store import OUT_DIR, get_passage, load_corpus
from vrag.text import content_hash, normalize

Status = Literal["verified", "not_verified", "unknown_source", "unable"]

STATUS_LABELS: dict[str, str] = {
    "verified": "\u2705 Verified",
    "not_verified": "\u274c Not verified",
    "unknown_source": "\u274c Unknown source",
    "unable": "\u26aa Unable to verify",
}
QUOTE_WARNING = "\u26a0\ufe0f Quote not found"

_QUOTE = re.compile(r"[\"\u201c]([^\"\u201c\u201d]+)[\"\u201d]")
_AFTER_QUOTE = re.compile(r"[\s.,;:)\]]*(" + _BRACKET.pattern + ")")
_HEX32 = re.compile(r"^0x[0-9a-fA-F]{64}$")
_UINT32_MAX = 2**32 - 1


@dataclass
class CitationResult:
    label: str
    status: Status
    detail: str = ""
    quote_warnings: list[str] = field(default_factory=list)  # quotes not found in the passage

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]


def quotes_for_label(answer_text: str, label: str) -> list[str]:
    """Double-quoted strings that are followed directly by a citation of `label`."""
    found = []
    for quote in _QUOTE.finditer(answer_text):
        after = _AFTER_QUOTE.match(answer_text, quote.end())
        if after and label in parse_citations(after.group(1)):
            found.append(quote.group(1))
    return found


def check_quotes(answer_text: str, label: str, passage_text: str) -> list[str]:
    """Quotes attributed to `label` that do not appear in the passage (after normalization)."""
    haystack = normalize(passage_text)
    return [q for q in quotes_for_label(answer_text, label) if normalize(q) not in haystack]


def check_citation(
    label: str,
    answer: Answer,
    config: Config,
    authorized: bool = False,
    displayed_text: str | None = None,
    w3: Web3 | None = None,
    out_dir: Path = OUT_DIR,
) -> CitationResult:
    """Verify one citation. `displayed_text` is the passage text shown to the user.

    It defaults to the retrieved text; the tamper demo passes a modified copy instead.
    """
    retrieved = answer.passages.get(label)
    if retrieved is None:
        return CitationResult(label, "unknown_source", "the answer cites a passage that was not retrieved")

    text = retrieved.text if displayed_text is None else displayed_text
    warnings = check_quotes(answer.text, label, text)
    ref = retrieved.ref

    corpus = load_corpus(ref.slug, out_dir)
    passage = get_passage(ref.slug, ref.doc_id, ref.page, ref.index, authorized, out_dir)
    if passage is None:
        return CitationResult(label, "not_verified", "passage is not in the corpus", warnings)
    if corpus.corpus_id is None:
        return CitationResult(label, "not_verified", "corpus has no on-chain ID", warnings)
    if passage.salt is None:
        return CitationResult(label, "unable", "no access to this private passage's salt", warnings)
    if (corpus.chain_id is not None and corpus.chain_id != config.expected_chain_id) or (
        corpus.registry and config.registry_address
        and corpus.registry.lower() != config.registry_address.lower()
    ):
        return CitationResult(
            label, "unable", "corpus was published to a different network or registry", warnings
        )

    recomputed = content_hash(text, passage.salt)  # from the displayed text, not the stored hash
    proof = merkle.proof(corpus.levels, passage.leaf_index)
    try:
        ok = chain.verify_passage(
            config, corpus.corpus_id, proof, ref.doc_id, ref.page, ref.index, recomputed, w3
        )
    except ChainError as exc:
        return CitationResult(label, "unable", str(exc), warnings)
    if ok:
        return CitationResult(label, "verified", "", warnings)
    return CitationResult(label, "not_verified", "the passage does not match the published root", warnings)


def check_answer(
    answer: Answer,
    config: Config,
    authorized: bool = False,
    displayed: dict[str, str] | None = None,
    w3: Web3 | None = None,
    out_dir: Path = OUT_DIR,
) -> list[CitationResult]:
    """Check every citation in the answer. `displayed` maps labels to modified texts (demo)."""
    displayed = displayed or {}
    return [
        check_citation(label, answer, config, authorized, displayed.get(label), w3, out_dir)
        for label in answer.citations
    ]


# --- receipts ----------------------------------------------------------------

def make_receipt(
    label: str,
    answer: Answer,
    config: Config,
    authorized: bool,
    out_dir: Path = OUT_DIR,
) -> dict[str, Any]:
    """Receipt for one citation: enough to verify it on-chain without the passage text.

    Contains the content hash, proof, and chain details only. Never text or salt.
    """
    retrieved = answer.passages.get(label)
    if retrieved is None:
        raise ValueError(f"unknown citation {label!r}")
    ref = retrieved.ref
    if ref.private and not authorized:
        raise PermissionError("receipts for private passages require an authorized session")
    corpus = load_corpus(ref.slug, out_dir)
    passage = get_passage(ref.slug, ref.doc_id, ref.page, ref.index, authorized, out_dir)
    if passage is None or passage.salt is None or corpus.corpus_id is None:
        raise ValueError(f"cannot make a receipt for {label}: passage or on-chain ID missing")
    return {
        "corpusId": corpus.corpus_id,
        "docId": ref.doc_id,
        "page": ref.page,
        "index": ref.index,
        "contentHash": "0x" + content_hash(retrieved.text, passage.salt).hex(),
        "proof": ["0x" + node.hex() for node in merkle.proof(corpus.levels, passage.leaf_index)],
        "registry": corpus.registry or config.registry_address,
        "chainId": corpus.chain_id if corpus.chain_id is not None else config.expected_chain_id,
    }


@dataclass
class ReceiptResult:
    status: Literal["verified", "not_verified", "rejected", "unable"]
    reason: str = ""
    corpus_name: str | None = None
    corpus_version: str | None = None


def _validate_receipt(receipt: Any) -> str | None:
    """Return a reason string if the receipt is malformed, else None."""
    if not isinstance(receipt, dict):
        return "receipt must be a JSON object"
    required = ("corpusId", "docId", "page", "index", "contentHash", "proof", "registry", "chainId")
    missing = [k for k in required if k not in receipt]
    if missing:
        return f"receipt is missing: {', '.join(missing)}"
    for key in ("corpusId", "chainId"):
        if type(receipt[key]) is not int or receipt[key] < 0:
            return f"{key} must be a non-negative integer"
    for key in ("page", "index"):
        if type(receipt[key]) is not int or not 0 <= receipt[key] <= _UINT32_MAX:
            return f"{key} must be an integer between 0 and {_UINT32_MAX}"
    if not isinstance(receipt["docId"], str) or not receipt["docId"]:
        return "docId must be a non-empty string"
    if not isinstance(receipt["contentHash"], str) or not _HEX32.match(receipt["contentHash"]):
        return "contentHash must be a 32-byte hex string"
    proof = receipt["proof"]
    if not isinstance(proof, list) or not all(isinstance(n, str) and _HEX32.match(n) for n in proof):
        return "proof must be a list of 32-byte hex strings"
    if not isinstance(receipt["registry"], str) or not Web3.is_address(receipt["registry"]):
        return "registry must be an address"
    return None


def verify_receipt(receipt: Any, config: Config, w3: Web3 | None = None) -> ReceiptResult:
    """Check a receipt on-chain. Rejects receipts for another chain or registry."""
    problem = _validate_receipt(receipt)
    if problem:
        return ReceiptResult("rejected", problem)
    if receipt["chainId"] != config.expected_chain_id:
        return ReceiptResult(
            "rejected",
            f"receipt is for chain ID {receipt['chainId']}, but this app uses "
            f"{config.expected_chain_id} ({config.network})",
        )
    if not config.registry_address:
        return ReceiptResult("rejected", "REGISTRY_ADDRESS is not set, so the registry cannot be matched")
    if Web3.to_checksum_address(receipt["registry"]) != Web3.to_checksum_address(config.registry_address):
        return ReceiptResult(
            "rejected",
            f"receipt is for registry {receipt['registry']}, but this app uses {config.registry_address}",
        )

    proof = [bytes.fromhex(n[2:]) for n in receipt["proof"]]
    try:
        w3 = w3 or chain.connect(config)
        ok = chain.verify_passage(
            config, receipt["corpusId"], proof, receipt["docId"], receipt["page"],
            receipt["index"], bytes.fromhex(receipt["contentHash"][2:]), w3,
        )
    except ChainError as exc:
        return ReceiptResult("unable", str(exc))

    name = version = None
    try:
        info = chain.get_corpus(config, receipt["corpusId"], w3)
        name, version = info["name"], info["version"]
    except ChainError:
        pass  # unknown corpus ID: verification already failed above
    if ok:
        return ReceiptResult("verified", "", name, version)
    return ReceiptResult("not_verified", "the receipt does not match the published root", name, version)
