"""Command line entry point: `python -m vrag <command>`."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from vrag import merkle
import requests
from web3 import Web3

from vrag import chain, rag, verify
from vrag.chain import ChainError, WrongChainError
from vrag.config import ROOT, check_auditor_token, load_config
from vrag.index import index_corpus
from vrag.ingest import ingest_folder
from vrag.rag import LLMError
from vrag.store import OUT_DIR, load_corpus, record_publication, save_corpus
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
        Path(args.folder), args.slug, args.name, args.version,
        private=args.private, pattern=args.pattern,
    )
    out_dir = Path(args.out_dir)
    try:  # keep the on-chain ID when the same content is ingested again
        previous = load_corpus(args.slug, out_dir)
        if previous.root == corpus.root and previous.corpus_id is not None:
            corpus.corpus_id, corpus.registry, corpus.chain_id = (
                previous.corpus_id,
                previous.registry,
                previous.chain_id,
            )
    except FileNotFoundError:
        pass
    path = save_corpus(corpus, out_dir)
    print(f"Ingested {len(corpus.passages)} passages into '{corpus.slug}' ({path})")
    print(f"Merkle root: 0x{corpus.root.hex()}")
    if corpus.private:
        print("Private corpus: text and salts stored in private.db only.")
    for name, reason in corpus.skipped:
        print(f"Skipped {name}: {reason}", file=sys.stderr)
    return 0


def cmd_deploy(_: argparse.Namespace) -> int:
    config = load_config()
    address = chain.deploy(config)
    print(f"CorpusRegistry deployed to {address} on {config.network}")
    if config.network == "glassnet":
        print(f"Explorer: {chain.address_url(config, address)}")
    print(f"Add this line to .env:  REGISTRY_ADDRESS={address}")
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    config = load_config()
    out_dir = Path(args.out_dir)
    corpus = load_corpus(args.slug, out_dir)
    if corpus.corpus_id is not None:
        print(
            f"error: corpus {args.slug!r} is already published as corpus ID {corpus.corpus_id}. "
            "Published roots are permanent; ingest with a new --version to publish a new one.",
            file=sys.stderr,
        )
        return 1
    w3 = chain.connect(config)
    corpus_id = chain.publish(corpus, config, w3)
    record_publication(args.slug, corpus_id, config.registry_address, w3.eth.chain_id, out_dir)
    print(f"Published {args.slug!r} ({len(corpus.passages)} passages) as corpus ID {corpus_id}")
    print(f"Root: 0x{corpus.root.hex()}")
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    count = index_corpus(args.slug, Path(args.out_dir))
    print(f"Indexed {count} passages from {args.slug!r}")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    config = load_config()
    authorized = False
    if args.auditor:
        if sys.stdin.isatty():
            token = getpass.getpass("Auditor token: ")
        else:  # piped input (scripts, tests): getpass cannot read it on Windows
            token = sys.stdin.readline().strip()
        if not check_auditor_token(token, config):
            print("error: wrong auditor token (or AUDITOR_TOKEN is not set in .env)", file=sys.stderr)
            return 1
        authorized = True

    out_dir = Path(args.out_dir)
    answer = rag.ask(args.question, authorized, args.provider, config, out_dir)
    if not answer.found:
        print(answer.text)
        return 0

    print(f"Answer ({answer.model} via {answer.provider}):")
    print(answer.text)
    try:
        w3 = chain.connect(config)
    except ChainError:
        w3 = None  # each citation will report "Unable to verify"
    results = verify.check_answer(answer, config, authorized, w3=w3, out_dir=out_dir)

    print("\nCitations:")
    if not results:
        print("  (the answer cites no passages)")
    for result in results:
        passage = answer.passages.get(result.label)
        where = passage.ref.passage_id if passage else "not among the retrieved passages"
        print(f"  [{result.label}] {result.status_label}  {where}")
        if result.detail:
            print(f"      {result.detail}")
        for quote in result.quote_warnings:
            print(f'      {verify.QUOTE_WARNING}: "{quote}"')

    cited = [answer.passages[r.label] for r in results if r.label in answer.passages]
    if cited:
        print("\nPassages:")
        for p in cited:
            print(f"  [{p.label}] (similarity {p.similarity:.2f}) {p.ref.passage_id}")
            print(f"      {p.text}")

    if args.export_receipts:
        export_dir = Path(args.export_receipts)
        for result in results:
            passage = answer.passages.get(result.label)
            if result.status == "verified" and passage and passage.ref.private:
                receipt = verify.make_receipt(result.label, answer, config, authorized, out_dir)
                export_dir.mkdir(parents=True, exist_ok=True)
                path = export_dir / f"receipt-{passage.ref.slug}-{result.label}.json"
                path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
                print(f"Receipt written: {path}")
    return 0


def cmd_verify_receipt(args: argparse.Namespace) -> int:
    config = load_config()
    path = Path(args.file)
    try:
        receipt = json.loads(path.read_text(encoding="utf-8-sig"))  # tolerate a Windows BOM
    except json.JSONDecodeError as exc:
        print(f"error: {path} is not valid JSON: {exc}", file=sys.stderr)
        return 1
    result = verify.verify_receipt(receipt, config)
    labels = {
        "verified": verify.STATUS_LABELS["verified"],
        "not_verified": verify.STATUS_LABELS["not_verified"],
        "unable": verify.STATUS_LABELS["unable"],
        "rejected": "\u274c Rejected",
    }
    print(labels[result.status])
    if result.corpus_name:
        print(f"Corpus: {result.corpus_name} ({result.corpus_version})")
    if result.reason:
        print(result.reason)
    return 0 if result.status == "verified" else 1


def cmd_check(_: argparse.Namespace) -> int:
    config = load_config()
    ok = True
    print(f"Network:  {config.network} (expected chain ID {config.expected_chain_id})")
    print(f"RPC URL:  {config.rpc_url}")
    try:
        w3 = Web3(Web3.HTTPProvider(config.rpc_url, request_kwargs={"timeout": 10}))
        actual = w3.eth.chain_id
        match = actual == config.expected_chain_id
        print(f"Chain ID: {actual} ({'OK' if match else 'MISMATCH'})")
        ok &= match
        if not config.registry_address:
            print("Registry: not set (run `python -m vrag deploy`)")
            ok = False
        elif not Web3.is_address(config.registry_address):
            print(f"Registry: {config.registry_address} is not a valid address")
            ok = False
        else:
            address = Web3.to_checksum_address(config.registry_address)
            has_code = len(w3.eth.get_code(address)) > 0
            status = "OK" if has_code else "NO CONTRACT AT THIS ADDRESS"
            print(f"Registry: {address} ({status})")
            ok &= has_code
            if has_code and match:
                print(f"Corpora:  {chain.corpus_count(config, w3)} published")
    except Exception as exc:
        print(f"Chain:    unreachable ({exc})")
        ok = False
    ok &= _check_llm(config)
    return 0 if ok else 1


def _check_llm(config) -> bool:
    """Print LLM provider status. Never prints the API key."""
    ok = True
    print(f"Public answers:  {config.llm_provider}")
    print(f"Private answers: {config.private_llm_provider}")
    if "anthropic" in (config.llm_provider, config.private_llm_provider):
        key = "set" if config.anthropic_api_key else "NOT SET (ANTHROPIC_API_KEY in .env)"
        model = config.anthropic_model or "NOT SET (ANTHROPIC_MODEL in .env)"
        print(f"Claude:   key {key}; model {model}")
        ok &= bool(config.anthropic_api_key and config.anthropic_model)
    try:
        url = config.ollama_url.rstrip("/") + "/api/tags"
        tags = requests.get(url, timeout=5).json()
        names = {m["name"] for m in tags.get("models", [])}
        print(f"Ollama:   reachable at {config.ollama_url}")
        found = config.ollama_model in names or f"{config.ollama_model}:latest" in names
        status = "installed" if found else f"missing (run `ollama pull {config.ollama_model}`)"
        print(f"  model {config.ollama_model}: {status}")
        ok &= found
    except Exception as exc:
        print(f"Ollama:   unreachable at {config.ollama_url} ({exc})")
        ok = False
    return ok


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vrag", description="Verifiable RAG citations")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="split PDF/TXT files into passages and build the tree")
    ingest.add_argument("folder")
    ingest.add_argument("--slug", required=True)
    ingest.add_argument("--name", required=True)
    ingest.add_argument("--version", required=True)
    ingest.add_argument("--private", action="store_true")
    ingest.add_argument("--pattern", default="*", help="only files matching this glob, e.g. 'public-*'")
    ingest.add_argument("--out-dir", default=str(OUT_DIR), help=argparse.SUPPRESS)
    ingest.set_defaults(func=cmd_ingest)

    sub.add_parser("deploy", help="deploy CorpusRegistry").set_defaults(func=cmd_deploy)

    publish = sub.add_parser("publish", help="publish a corpus root on-chain")
    publish.add_argument("--slug", required=True)
    publish.add_argument("--out-dir", default=str(OUT_DIR), help=argparse.SUPPRESS)
    publish.set_defaults(func=cmd_publish)

    index = sub.add_parser("index", help="embed a published corpus and add it to the search index")
    index.add_argument("--slug", required=True)
    index.add_argument("--out-dir", default=str(OUT_DIR), help=argparse.SUPPRESS)
    index.set_defaults(func=cmd_index)

    ask = sub.add_parser("ask", help="ask a question and verify the citations")
    ask.add_argument("question")
    ask.add_argument("--auditor", action="store_true", help="unlock private corpora (asks for the token)")
    ask.add_argument("--provider", choices=["anthropic", "ollama"], help="force a provider (public passages only for anthropic)")
    ask.add_argument("--export-receipts", metavar="DIR", help="write receipts for verified private citations")
    ask.add_argument("--out-dir", default=str(OUT_DIR), help=argparse.SUPPRESS)
    ask.set_defaults(func=cmd_ask)

    receipt = sub.add_parser("verify-receipt", help="verify a citation receipt on-chain")
    receipt.add_argument("file")
    receipt.set_defaults(func=cmd_verify_receipt)

    sub.add_parser("check", help="show network, registry, and Ollama status").set_defaults(
        func=cmd_check
    )

    sub.add_parser("fixtures", help="write blockchain/test/fixtures/merkle.json").set_defaults(
        func=cmd_fixtures
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy code page
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return args.func(args)
    except (ChainError, WrongChainError, LLMError, ValueError, FileNotFoundError, NotADirectoryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
