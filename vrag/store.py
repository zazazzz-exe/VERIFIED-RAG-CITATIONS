"""Corpus storage: public corpus JSON files and the private SQLite database.

Public corpora keep passage text in `out/corpora/<slug>.json`. Private corpora keep only
hashes and the Merkle tree there; their text and salts live in `out/private.db`.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from vrag.config import ROOT
from vrag.text import ZERO32

OUT_DIR = ROOT / "out"

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class Passage:
    slug: str
    doc_id: str
    page: int
    index: int
    text: str | None  # None for private passages when the caller is not authorized
    content_hash: bytes
    leaf_index: int
    private: bool
    salt: bytes | None = field(default=ZERO32, repr=False)  # None when hidden

    @property
    def passage_id(self) -> str:
        return f"{self.slug}:{self.doc_id}:p{self.page}:{self.index}"


@dataclass
class Corpus:
    slug: str
    name: str
    version: str
    private: bool
    passages: list[Passage]
    levels: list[list[bytes]]
    corpus_id: int | None = None
    registry: str | None = None
    chain_id: int | None = None
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (file name, reason)

    @property
    def root(self) -> bytes:
        return self.levels[-1][0]


def validate_slug(slug: str) -> str:
    """Slugs become file names, so only lowercase letters, digits, and hyphens are allowed."""
    if not _SLUG.fullmatch(slug):
        raise ValueError(f"invalid slug {slug!r}: use lowercase letters, digits, and hyphens")
    return slug


def corpus_path(slug: str, out_dir: Path = OUT_DIR) -> Path:
    return out_dir / "corpora" / f"{validate_slug(slug)}.json"


def private_db_path(out_dir: Path = OUT_DIR) -> Path:
    return out_dir / "private.db"


def _hx(data: bytes) -> str:
    return "0x" + data.hex()


def _unhx(value: str) -> bytes:
    return bytes.fromhex(value[2:] if value.startswith("0x") else value)


def _open_private_db(out_dir: Path) -> sqlite3.Connection:
    path = private_db_path(out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS passages ("
        "slug TEXT, doc_id TEXT, page INT, idx INT, text TEXT, salt BLOB, "
        "PRIMARY KEY (slug, doc_id, page, idx))"
    )
    return conn


def save_corpus(corpus: Corpus, out_dir: Path = OUT_DIR) -> Path:
    """Write the corpus file (and private.db rows for private corpora). Returns the file path."""
    path = corpus_path(corpus.slug, out_dir)
    passages = []
    for p in corpus.passages:
        entry: dict = {
            "docId": p.doc_id,
            "page": p.page,
            "index": p.index,
            "contentHash": _hx(p.content_hash),
            "leafIndex": p.leaf_index,
        }
        if not corpus.private:
            entry["text"] = p.text  # private text and salts are never written to this file
        passages.append(entry)
    data = {
        "slug": corpus.slug,
        "name": corpus.name,
        "version": corpus.version,
        "private": corpus.private,
        "corpusId": corpus.corpus_id,
        "root": _hx(corpus.root),
        "registry": corpus.registry,
        "chainId": corpus.chain_id,
        "passages": passages,
        "levels": [[_hx(n) for n in level] for level in corpus.levels],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    if corpus.private:
        conn = _open_private_db(out_dir)
        try:
            with conn:
                conn.execute("DELETE FROM passages WHERE slug = ?", (corpus.slug,))
                conn.executemany(
                    "INSERT INTO passages (slug, doc_id, page, idx, text, salt) VALUES (?,?,?,?,?,?)",
                    [
                        (corpus.slug, p.doc_id, p.page, p.index, p.text, p.salt)
                        for p in corpus.passages
                    ],
                )
        finally:
            conn.close()
    return path


def load_corpus(slug: str, out_dir: Path = OUT_DIR) -> Corpus:
    """Load a corpus file. Private passages come back without text or salt."""
    path = corpus_path(slug, out_dir)
    if not path.exists():
        raise FileNotFoundError(f"corpus {slug!r} not found at {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    private = bool(data["private"])
    passages = [
        Passage(
            slug=data["slug"],
            doc_id=p["docId"],
            page=p["page"],
            index=p["index"],
            text=None if private else p["text"],
            content_hash=_unhx(p["contentHash"]),
            leaf_index=p["leafIndex"],
            private=private,
            salt=None if private else ZERO32,
        )
        for p in data["passages"]
    ]
    return Corpus(
        slug=data["slug"],
        name=data["name"],
        version=data["version"],
        private=private,
        passages=passages,
        levels=[[_unhx(n) for n in level] for level in data["levels"]],
        corpus_id=data.get("corpusId"),
        registry=data.get("registry"),
        chain_id=data.get("chainId"),
    )


def record_publication(
    slug: str, corpus_id: int, registry: str, chain_id: int, out_dir: Path = OUT_DIR
) -> None:
    """Store the on-chain corpus ID in the corpus file (edits the JSON only)."""
    path = corpus_path(slug, out_dir)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["corpusId"] = corpus_id
    data["registry"] = registry
    data["chainId"] = chain_id
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def get_passage(
    slug: str,
    doc_id: str,
    page: int,
    index: int,
    authorized: bool = False,
    out_dir: Path = OUT_DIR,
) -> Passage | None:
    """Look up one passage. Private text and salt are returned only when `authorized`."""
    corpus = load_corpus(slug, out_dir)
    found = next(
        (p for p in corpus.passages if (p.doc_id, p.page, p.index) == (doc_id, page, index)),
        None,
    )
    if found is None or not found.private or not authorized:
        return found
    conn = _open_private_db(out_dir)
    try:
        row = conn.execute(
            "SELECT text, salt FROM passages WHERE slug=? AND doc_id=? AND page=? AND idx=?",
            (slug, doc_id, page, index),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return found
    return Passage(
        slug=found.slug,
        doc_id=found.doc_id,
        page=found.page,
        index=found.index,
        text=row[0],
        content_hash=found.content_hash,
        leaf_index=found.leaf_index,
        private=True,
        salt=bytes(row[1]),
    )
