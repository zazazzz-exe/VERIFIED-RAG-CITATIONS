"""Command line entry point: `python -m vrag <command>`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from vrag import merkle
from vrag.config import ROOT
from vrag.ingest import ingest_folder
from vrag.store import OUT_DIR, save_corpus
from vrag.text import ZERO32, content_hash, normalize

FIXTURE_PATH = ROOT / "blockchain" / "test" / "fixtures" / "merkle.json"

# Fixed sample passages: (docId, page, index, text, salt). Covers public and salted passages,
# multiple documents and pages, extra whitespace, and non-ASCII text. All content is fictional.
_FIXTURE_PASSAGES: list[tuple[str, int, int, str, bytes]] = [
    ("sample-a", 1, 0, "The  quick brown fox\njumps over the lazy dog.", ZERO32),
    ("sample-a", 1, 1, "Second passage on the first page.", ZERO32),
    ("sample-a", 2, 0, "Caf\u00e9 \u2013 r\u00e9sum\u00e9 of the fictional budget: 1,250,000 units.", ZERO32),
    ("sample-b", 1, 0, "e\u0301cole (decomposed accent, normalized to NFC).", ZERO32),
    ("sample-b", 3, 2, "Private fictional passage kept with a salt.", bytes(range(1, 33))),
]


def build_fixture() -> dict:
    """Build the cross-language Merkle fixture from the fixed sample passages."""
    entries = []
    for doc_id, page, index, text, salt in _FIXTURE_PASSAGES:
        normalized = normalize(text)
        ch = content_hash(text, salt)
        entries.append(
            {
                "docId": doc_id,
                "page": page,
                "index": index,
                "text": normalized,
                "salt": "0x" + salt.hex(),
                "contentHash": "0x" + ch.hex(),
                "leaf": "0x" + merkle.leaf_hash(doc_id, page, index, ch).hex(),
            }
        )
    levels = merkle.build_tree([bytes.fromhex(e["leaf"][2:]) for e in entries])
    for i, entry in enumerate(entries):
        entry["proof"] = ["0x" + node.hex() for node in merkle.proof(levels, i)]
    return {
        "name": "fixture-corpus",
        "version": "v1",
        "root": "0x" + merkle.root(levels).hex(),
        "passages": entries,
    }


def cmd_fixtures(_: argparse.Namespace) -> int:
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fixture = build_fixture()
    FIXTURE_PATH.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {FIXTURE_PATH} ({len(fixture['passages'])} passages, root {fixture['root']})")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    corpus = ingest_folder(
        Path(args.folder), args.slug, args.name, args.version, private=args.private
    )
    path = save_corpus(corpus, Path(args.out_dir))
    print(f"Ingested {len(corpus.passages)} passages into '{corpus.slug}' ({path})")
    print(f"Merkle root: 0x{corpus.root.hex()}")
    if corpus.private:
        print("Private corpus: text and salts stored in private.db only.")
    for name, reason in corpus.skipped:
        print(f"Skipped {name}: {reason}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vrag", description="Verifiable RAG citations")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="split PDF/TXT files into passages and build the tree")
    ingest.add_argument("folder")
    ingest.add_argument("--slug", required=True)
    ingest.add_argument("--name", required=True)
    ingest.add_argument("--version", required=True)
    ingest.add_argument("--private", action="store_true")
    ingest.add_argument("--out-dir", default=str(OUT_DIR), help=argparse.SUPPRESS)
    ingest.set_defaults(func=cmd_ingest)

    sub.add_parser("fixtures", help="write blockchain/test/fixtures/merkle.json").set_defaults(
        func=cmd_fixtures
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    return args.func(args)
