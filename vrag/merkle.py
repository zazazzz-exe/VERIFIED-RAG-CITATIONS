"""Merkle tree matching OpenZeppelin MerkleProof (commutative keccak256, sorted pairs)."""

from __future__ import annotations

from eth_abi import encode
from eth_utils import keccak


def leaf_hash(doc_id: str, page: int, index: int, content_hash: bytes) -> bytes:
    """Double-hashed leaf, same as OpenZeppelin StandardMerkleTree."""
    if len(content_hash) != 32:
        raise ValueError("content_hash must be 32 bytes")
    encoded = encode(
        ["string", "uint32", "uint32", "bytes32"], [doc_id, page, index, content_hash]
    )
    return keccak(keccak(encoded))


def hash_pair(a: bytes, b: bytes) -> bytes:
    """keccak256 of the two nodes sorted ascending."""
    return keccak(a + b) if a <= b else keccak(b + a)


def build_tree(leaves: list[bytes]) -> list[list[bytes]]:
    """Return levels from leaves (level 0) to root (last level, one node).

    Leaves keep their given order. An odd last node is promoted unchanged.
    """
    if not leaves:
        raise ValueError("cannot build a Merkle tree with no leaves")
    levels = [list(leaves)]
    while len(levels[-1]) > 1:
        current = levels[-1]
        nxt = [
            hash_pair(current[i], current[i + 1]) if i + 1 < len(current) else current[i]
            for i in range(0, len(current), 2)
        ]
        levels.append(nxt)
    return levels


def root(levels: list[list[bytes]]) -> bytes:
    return levels[-1][0]


def proof(levels: list[list[bytes]], leaf_index: int) -> list[bytes]:
    """Sibling hashes from the leaf up to (not including) the root."""
    if not 0 <= leaf_index < len(levels[0]):
        raise IndexError(f"leaf_index {leaf_index} out of range")
    result: list[bytes] = []
    idx = leaf_index
    for level in levels[:-1]:
        sibling = idx ^ 1
        if sibling < len(level):  # no sibling means the node was promoted
            result.append(level[sibling])
        idx //= 2
    return result


def verify(proof: list[bytes], root: bytes, leaf: bytes) -> bool:
    """Mirror of OpenZeppelin MerkleProof.verify."""
    computed = leaf
    for sibling in proof:
        computed = hash_pair(computed, sibling)
    return computed == root
