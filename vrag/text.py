"""Text normalization and content hashing (format fixed in .kiro/steering/tech.md)."""

from __future__ import annotations

import re
import unicodedata

from eth_abi import encode
from eth_utils import keccak

ZERO32 = b"\x00" * 32

_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Unicode NFC, collapse every whitespace run to one space, strip the ends."""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", text)).strip()


def content_hash(text: str, salt: bytes = ZERO32) -> bytes:
    """keccak256(abi.encode(bytes32 salt, string normalizedText)).

    Public passages use the all-zero salt. Private passages use a random 32-byte salt.
    """
    if len(salt) != 32:
        raise ValueError(f"salt must be 32 bytes, got {len(salt)}")
    return keccak(encode(["bytes32", "string"], [salt, normalize(text)]))
