import pytest
from eth_abi import encode
from eth_utils import keccak

from vrag import merkle
from vrag.text import ZERO32, content_hash, normalize


# --- normalization -----------------------------------------------------------

def test_normalize_collapses_and_strips_whitespace():
    assert normalize("  a \t b\n\n c  ") == "a b c"


def test_normalize_applies_nfc():
    decomposed = "e\u0301"  # e + combining acute
    assert normalize(decomposed) == "\u00e9"


def test_normalize_is_idempotent():
    once = normalize(" Caf\u00e9 \n au  lait ")
    assert normalize(once) == once


def test_normalize_unicode_whitespace():
    assert normalize("a\u00a0\u2003b") == "a b"


# --- content hash ------------------------------------------------------------

def test_content_hash_matches_spec_formula():
    expected = keccak(encode(["bytes32", "string"], [ZERO32, "hello world"]))
    assert content_hash("hello world") == expected


def test_content_hash_ignores_whitespace_differences():
    assert content_hash(" hello \n world ") == content_hash("hello world")


def test_content_hash_changes_with_text():
    assert content_hash("hello world") != content_hash("hello w0rld")


def test_salted_hash_differs_from_unsalted():
    salt = bytes(range(32))
    assert content_hash("secret", salt) != content_hash("secret")
    assert content_hash("secret", salt) == keccak(encode(["bytes32", "string"], [salt, "secret"]))


def test_different_salts_give_different_hashes():
    assert content_hash("x", b"\x01" * 32) != content_hash("x", b"\x02" * 32)


def test_bad_salt_length_rejected():
    with pytest.raises(ValueError):
        content_hash("x", b"\x00" * 31)


# --- leaf and pair hashing ---------------------------------------------------

def test_leaf_hash_is_double_keccak():
    ch = content_hash("p")
    inner = keccak(encode(["string", "uint32", "uint32", "bytes32"], ["doc", 3, 0, ch]))
    assert merkle.leaf_hash("doc", 3, 0, ch) == keccak(inner)


def test_leaf_hash_depends_on_every_field():
    ch = content_hash("p")
    base = merkle.leaf_hash("doc", 1, 0, ch)
    assert base != merkle.leaf_hash("doc2", 1, 0, ch)
    assert base != merkle.leaf_hash("doc", 2, 0, ch)
    assert base != merkle.leaf_hash("doc", 1, 1, ch)
    assert base != merkle.leaf_hash("doc", 1, 0, content_hash("q"))


def test_hash_pair_is_commutative():
    a, b = keccak(b"a"), keccak(b"b")
    assert merkle.hash_pair(a, b) == merkle.hash_pair(b, a)
    lo, hi = sorted([a, b])
    assert merkle.hash_pair(a, b) == keccak(lo + hi)


# --- trees -------------------------------------------------------------------

def make_leaves(n: int) -> list[bytes]:
    return [merkle.leaf_hash("doc", 1, i, content_hash(f"passage {i}")) for i in range(n)]


def test_empty_tree_is_an_error():
    with pytest.raises(ValueError):
        merkle.build_tree([])


def test_single_leaf_tree():
    leaves = make_leaves(1)
    levels = merkle.build_tree(leaves)
    assert merkle.root(levels) == leaves[0]
    assert merkle.proof(levels, 0) == []
    assert merkle.verify([], merkle.root(levels), leaves[0])


def test_two_leaf_tree():
    leaves = make_leaves(2)
    levels = merkle.build_tree(leaves)
    assert merkle.root(levels) == merkle.hash_pair(leaves[0], leaves[1])
    assert merkle.proof(levels, 0) == [leaves[1]]
    assert merkle.proof(levels, 1) == [leaves[0]]


def test_three_leaf_tree_promotes_odd_node():
    leaves = make_leaves(3)
    levels = merkle.build_tree(leaves)
    assert len(levels) == 3
    assert levels[1] == [merkle.hash_pair(leaves[0], leaves[1]), leaves[2]]  # promoted, not duplicated
    assert merkle.root(levels) == merkle.hash_pair(levels[1][0], leaves[2])
    assert merkle.proof(levels, 2) == [levels[1][0]]


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 7, 10, 16, 17])
def test_every_proof_verifies(n):
    leaves = make_leaves(n)
    levels = merkle.build_tree(leaves)
    r = merkle.root(levels)
    for i, leaf in enumerate(leaves):
        assert merkle.verify(merkle.proof(levels, i), r, leaf)


def test_ten_leaf_tree_rejects_tampering():
    leaves = make_leaves(10)
    levels = merkle.build_tree(leaves)
    r = merkle.root(levels)
    p = merkle.proof(levels, 4)
    assert not merkle.verify(p, r, keccak(b"other leaf"))
    bad = [keccak(b"x")] + p[1:]
    assert not merkle.verify(bad, r, leaves[4])
    assert not merkle.verify(p[:-1], r, leaves[4])
    assert not merkle.verify(p, keccak(b"other root"), leaves[4])


def test_leaf_order_changes_root_only_when_pairing_changes():
    leaves = make_leaves(4)
    assert merkle.root(merkle.build_tree(leaves)) != merkle.root(
        merkle.build_tree([leaves[0], leaves[2], leaves[1], leaves[3]])
    )


def test_build_tree_is_deterministic():
    leaves = make_leaves(10)
    assert merkle.build_tree(leaves) == merkle.build_tree(list(leaves))


def test_proof_index_out_of_range():
    levels = merkle.build_tree(make_leaves(3))
    with pytest.raises(IndexError):
        merkle.proof(levels, 3)
