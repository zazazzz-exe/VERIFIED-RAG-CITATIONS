"""Local search index: Chroma collection with `all-MiniLM-L6-v2` embeddings.

Embeddings are computed here with a pinned local model (ONNX, CPU, no server or API key)
and passed to Chroma explicitly. Chroma stores only vectors and metadata, never passage
text, so private text stays in `out/private.db`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

from vrag.store import OUT_DIR, get_passage, load_corpus

COLLECTION = "passages"
_embedder: ONNXMiniLM_L6_V2 | None = None


@dataclass(frozen=True)
class PassageRef:
    slug: str
    doc_id: str
    page: int
    index: int
    private: bool

    @property
    def passage_id(self) -> str:
        return f"{self.slug}:{self.doc_id}:p{self.page}:{self.index}"


def embed(texts: list[str]) -> list[list[float]]:
    """Embed texts with the local MiniLM model (384 dimensions)."""
    global _embedder
    if _embedder is None:
        _embedder = ONNXMiniLM_L6_V2()
    return [[float(x) for x in vector] for vector in _embedder(texts)]


def _collection(out_dir: Path):
    client = chromadb.PersistentClient(path=str(out_dir / "chroma"))
    return client.get_or_create_collection(
        COLLECTION, metadata={"hnsw:space": "cosine"}, embedding_function=None
    )


def index_corpus(slug: str, out_dir: Path = OUT_DIR) -> int:
    """(Re)index one published corpus. Returns the number of passages indexed.

    Raises ValueError if the corpus has not been published yet.
    """
    corpus = load_corpus(slug, out_dir)
    if corpus.corpus_id is None:
        raise ValueError(
            f"corpus {slug!r} is not published yet; run `python -m vrag publish --slug {slug}` first"
        )
    texts: list[str] = []
    for p in corpus.passages:
        # Private text is read from private.db here, in this local process only.
        full = get_passage(slug, p.doc_id, p.page, p.index, authorized=True, out_dir=out_dir)
        if full is None or full.text is None:
            raise ValueError(f"missing text for {p.passage_id}; re-ingest the corpus")
        texts.append(full.text)

    collection = _collection(out_dir)
    collection.delete(where={"slug": slug})  # re-indexing replaces the old entries
    collection.add(
        ids=[p.passage_id for p in corpus.passages],
        embeddings=embed(texts),
        metadatas=[
            {
                "slug": slug,
                "private": corpus.private,
                "docId": p.doc_id,
                "page": p.page,
                "index": p.index,
            }
            for p in corpus.passages
        ],
    )
    return len(corpus.passages)


def search(
    question: str, k: int = 5, authorized: bool = False, out_dir: Path = OUT_DIR
) -> list[tuple[PassageRef, float]]:
    """Top-k passages by cosine similarity (1.0 = identical), best first.

    Unauthorized searches are filtered to public corpora inside Chroma, so private passages
    are never even candidates.
    """
    collection = _collection(out_dir)
    where = None if authorized else {"private": False}
    available = len(collection.get(where=where, include=[])["ids"])
    if available == 0 or k <= 0:
        return []
    result = collection.query(
        query_embeddings=embed([question]),
        n_results=min(k, available),
        where=where,
        include=["metadatas", "distances"],
    )
    hits = []
    for meta, distance in zip(result["metadatas"][0], result["distances"][0]):
        ref = PassageRef(
            slug=meta["slug"],
            doc_id=meta["docId"],
            page=int(meta["page"]),
            index=int(meta["index"]),
            private=bool(meta["private"]),
        )
        hits.append((ref, 1.0 - float(distance)))
    return hits
