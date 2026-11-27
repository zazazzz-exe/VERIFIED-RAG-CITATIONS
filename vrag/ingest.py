"""PDF/TXT extraction and passage splitting."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pymupdf

from vrag import merkle
from vrag.store import Corpus, Passage, validate_slug
from vrag.text import ZERO32, content_hash, normalize

MIN_CHARS = 300
MAX_CHARS = 1200

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_SUPPORTED = {".pdf", ".txt"}


def extract_pages(path: Path) -> list[str]:
    """Return the text of each page. A TXT file is a single page (page 1)."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        with pymupdf.open(path) as doc:
            return [page.get_text() for page in doc]
    if suffix == ".txt":
        return [path.read_text(encoding="utf-8-sig")]
    raise ValueError(f"unsupported file type: {path.suffix}")


def _chunk_paragraph(paragraph: str) -> list[str]:
    """Split a paragraph longer than MAX_CHARS on sentence boundaries."""
    chunks: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(paragraph):
        while len(sentence) > MAX_CHARS:  # one huge sentence: cut at whitespace
            cut = sentence.rfind(" ", 0, MAX_CHARS)
            cut = cut if cut > 0 else MAX_CHARS
            if current:
                chunks.append(current)
                current = ""
            chunks.append(sentence[:cut])
            sentence = sentence[cut:].lstrip()
        if not sentence:
            continue
        if current and len(current) + 1 + len(sentence) <= MAX_CHARS:
            current += " " + sentence
        else:
            if current:
                chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    return chunks


def split_passages(page_text: str) -> list[str]:
    """Split page text into passages of about MIN_CHARS to MAX_CHARS (normalized text).

    Paragraphs are separated by blank lines. Short paragraphs are merged with the following
    ones, long paragraphs are split on sentences. A passage can still be shorter than
    MIN_CHARS when merging would exceed MAX_CHARS or it is the last one on the page.
    """
    units: list[str] = []
    for raw in _PARAGRAPH_BREAK.split(page_text):
        paragraph = normalize(raw)
        if not paragraph:
            continue
        units.extend(_chunk_paragraph(paragraph) if len(paragraph) > MAX_CHARS else [paragraph])

    passages: list[str] = []
    buffer = ""
    for unit in units:
        if buffer and len(buffer) < MIN_CHARS and len(buffer) + 1 + len(unit) <= MAX_CHARS:
            buffer += " " + unit
        else:
            if buffer:
                passages.append(buffer)
            buffer = unit
    if buffer:
        passages.append(buffer)
    return passages


def ingest_folder(
    folder: Path, slug: str, name: str, version: str, private: bool = False, pattern: str = "*"
) -> Corpus:
    """Turn the PDF/TXT files in `folder` into a corpus with a Merkle tree.

    Only files whose name matches the glob `pattern` are used (default: all). Deterministic
    for public corpora: files are sorted by name and passages keep reading
    order. Unreadable or empty files are skipped and listed in `Corpus.skipped`. Private
    corpora get a random salt per passage, so their root changes on every ingest.
    """
    validate_slug(slug)
    folder = Path(folder)
    if not folder.is_dir():
        raise NotADirectoryError(f"not a folder: {folder}")

    files = sorted(
        (f for f in folder.glob(pattern) if f.is_file() and f.suffix.lower() in _SUPPORTED),
        key=lambda f: f.name,
    )
    stems = [f.stem for f in files]
    duplicates = sorted({s for s in stems if stems.count(s) > 1})
    if duplicates:
        raise ValueError(f"files with the same name but different extensions: {duplicates}")

    passages: list[Passage] = []
    skipped: list[tuple[str, str]] = []
    for file in files:
        try:
            pages = extract_pages(file)
        except Exception as exc:  # unreadable, encrypted, corrupt, bad encoding
            skipped.append((file.name, f"cannot read: {exc}"))
            continue
        file_passages: list[Passage] = []
        for page_no, page_text in enumerate(pages, start=1):
            for index, text in enumerate(split_passages(page_text)):
                salt = os.urandom(32) if private else ZERO32
                ch = content_hash(text, salt)
                file_passages.append(
                    Passage(
                        slug=slug,
                        doc_id=file.stem,
                        page=page_no,
                        index=index,
                        text=text,
                        content_hash=ch,
                        leaf_index=len(passages) + len(file_passages),
                        private=private,
                        salt=salt,
                    )
                )
        if not file_passages:
            skipped.append((file.name, "no extractable text"))
            continue
        passages.extend(file_passages)

    if not passages:
        raise ValueError(f"no passages found in {folder}; skipped: {skipped}")

    leaves = [merkle.leaf_hash(p.doc_id, p.page, p.index, p.content_hash) for p in passages]
    return Corpus(
        slug=slug,
        name=name,
        version=version,
        private=private,
        passages=passages,
        levels=merkle.build_tree(leaves),
        skipped=skipped,
    )
