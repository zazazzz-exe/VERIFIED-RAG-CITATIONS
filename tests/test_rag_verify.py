import copy
import json
from types import SimpleNamespace

import pytest

from vrag import chain, rag, verify
from vrag.chain import ChainError
from vrag.cli import main
from vrag.config import ROOT, Config, check_auditor_token
from vrag.index import PassageRef, index_corpus
from vrag.ingest import ingest_folder
from vrag.rag import (
    NOT_FOUND_TEXT, Answer, PrivateDataError, RetrievedPassage, build_prompt,
    choose_provider, generate, parse_citations,
)
from vrag.store import record_publication, save_corpus
from vrag.verify import check_citation, check_quotes, quotes_for_label, verify_receipt

HARDHAT_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
SAMPLES = ROOT / "data" / "samples"
REGISTRY = "0x5FbDB2315678afecb367f032d93F642f64180aa3"


def cfg(**kw) -> Config:
    base = dict(network="local", registry_address=REGISTRY, anthropic_api_key="sk-ant-test",
                anthropic_model="claude-test", min_similarity=0.35)
    return Config(**{**base, **kw})


def passage(label, text="Some passage text.", private=False, slug="pub"):
    ref = PassageRef(slug, "public-merkle-trees", 1, 0, private)
    return RetrievedPassage(label, ref, text, 0.9)


# --- citation parsing --------------------------------------------------------

def test_parse_citations_in_order_without_duplicates():
    assert parse_citations("A [P2]. B [P1]. C [P2].") == ["P2", "P1"]


def test_parse_citations_handles_groups_and_spacing():
    assert parse_citations("x [P1, P3] y [ P2 ] z [P1,P4]") == ["P1", "P3", "P2", "P4"]


def test_parse_citations_ignores_look_alikes():
    assert parse_citations("no cites (P1) [Q1] [P] [1] P2") == []


def test_parse_citations_unknown_numbers_are_kept_for_verification():
    assert parse_citations("see [P9]") == ["P9"]


# --- quote check -------------------------------------------------------------

def test_quote_found_after_normalization():
    text = 'It says "The  root\ncommits to every leaf" [P1].'
    assert check_quotes(text, "P1", "The root commits to every leaf. If any leaf changes...") == []


def test_quote_not_found_is_reported():
    text = 'It says "the root is secret" [P1].'
    assert check_quotes(text, "P1", "The root commits to every leaf.") == ["the root is secret"]


def test_quote_is_attributed_only_to_the_label_that_follows():
    text = 'A "first quote" [P1] and "second quote" [P2].'
    assert quotes_for_label(text, "P1") == ["first quote"]
    assert quotes_for_label(text, "P2") == ["second quote"]
    assert quotes_for_label(text, "P3") == []


def test_quote_with_punctuation_smart_quotes_and_groups():
    assert quotes_for_label('He wrote \u201cexact words\u201d, [P1].', "P1") == ["exact words"]
    assert quotes_for_label('"a b" [P1, P2]', "P2") == ["a b"]
    assert quotes_for_label('"unattributed quote" and then [P1] later', "P1") == []


# --- provider routing --------------------------------------------------------

def test_public_questions_use_the_configured_provider():
    assert choose_provider(False, cfg()) == "anthropic"
    assert choose_provider(False, cfg(llm_provider="ollama")) == "ollama"
    assert choose_provider(False, cfg(), override="ollama") == "ollama"


def test_private_questions_use_ollama():
    assert choose_provider(True, cfg()) == "ollama"
    assert choose_provider(True, cfg(), override="ollama") == "ollama"


def test_forcing_claude_on_private_is_refused():
    with pytest.raises(PrivateDataError):
        choose_provider(True, cfg(), override="anthropic")


def test_unknown_override_rejected():
    with pytest.raises(ValueError):
        choose_provider(False, cfg(), override="gpt")


def test_private_text_never_reaches_the_anthropic_client(monkeypatch):
    called = []
    monkeypatch.setattr(rag, "_call_anthropic", lambda *a: called.append(a) or "x")
    with pytest.raises(PrivateDataError):
        generate("anthropic", cfg(), "q?", [passage("P1", private=True)])
    assert called == []  # refused before any call


def test_ask_sends_private_questions_to_ollama_only(tmp_path, monkeypatch):
    for corpus, cid in ((ingest_folder(SAMPLES, "pub", "Pub", "v1", pattern="public-*"), 0),
                        (ingest_folder(SAMPLES, "priv", "Priv", "v1", private=True, pattern="private-*"), 1)):
        save_corpus(corpus, tmp_path)
        record_publication(corpus.slug, cid, REGISTRY, 31337, tmp_path)
        index_corpus(corpus.slug, tmp_path)
    claude, ollama = [], []
    monkeypatch.setattr(rag, "_call_anthropic", lambda c, s, p: claude.append(p) or "Claude [P1]")
    monkeypatch.setattr(rag, "_call_ollama", lambda c, s, p: ollama.append(p) or "Ollama [P1]")

    private_q = "How much is allocated to the Riverside Footbridge?"
    answer = rag.ask(private_q, authorized=True, config=cfg(), out_dir=tmp_path)
    assert (answer.provider, answer.model) == ("ollama", "gemma3:4b")
    assert claude == [] and len(ollama) == 1 and "Westbrook" in ollama[0]

    public_q = "What is a Merkle proof?"
    answer = rag.ask(public_q, authorized=True, config=cfg(), out_dir=tmp_path)
    assert (answer.provider, answer.model) == ("anthropic", "claude-test")
    assert len(claude) == 1 and "Westbrook" not in claude[0]

    # unauthorized: private passages are not even retrieved, so nothing mentions them
    answer = rag.ask(private_q, authorized=False, config=cfg(), out_dir=tmp_path)
    assert not answer.found and answer.text == NOT_FOUND_TEXT
    assert len(claude) == 1  # no extra LLM call when nothing relevant is found


def test_irrelevant_question_makes_no_llm_call(tmp_path, monkeypatch):
    corpus = ingest_folder(SAMPLES, "pub", "Pub", "v1", pattern="public-*")
    save_corpus(corpus, tmp_path)
    record_publication("pub", 0, REGISTRY, 31337, tmp_path)
    index_corpus("pub", tmp_path)
    monkeypatch.setattr(rag, "_call_anthropic", lambda *a: pytest.fail("LLM must not be called"))
    answer = rag.ask("What is the capital of France?", config=cfg(), out_dir=tmp_path)
    assert not answer.found and answer.provider is None


def test_prompt_labels_passages_and_includes_the_question():
    prompt = build_prompt("Why?", [passage("P1", "alpha"), passage("P2", "beta")])
    assert "[P1] alpha" in prompt and "[P2] beta" in prompt and prompt.endswith("Question: Why?")


def test_llm_not_configured_errors_do_not_leak_the_key():
    with pytest.raises(rag.LLMError, match="ANTHROPIC_API_KEY"):
        rag._call_anthropic(cfg(anthropic_api_key=""), "s", "p")
    with pytest.raises(rag.LLMError, match="ANTHROPIC_MODEL"):
        rag._call_anthropic(cfg(anthropic_model=""), "s", "p")


def test_ollama_unreachable_error_names_the_url(monkeypatch):
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(rag.requests, "post", boom)
    with pytest.raises(rag.LLMError, match="http://localhost:9"):
        rag._call_ollama(cfg(ollama_url="http://localhost:9"), "s", "p")


def test_auditor_token_check():
    config = cfg(auditor_token="s3cret")
    assert check_auditor_token("s3cret", config)
    assert not check_auditor_token("wrong", config)
    assert not check_auditor_token("", config)
    assert not check_auditor_token("", cfg(auditor_token=""))  # unset token disables private access


# --- citation checks (chain mocked) ------------------------------------------

@pytest.fixture
def published(tmp_path):
    corpus = ingest_folder(SAMPLES, "pub", "Pub", "v1", pattern="public-*")
    save_corpus(corpus, tmp_path)
    record_publication("pub", 0, REGISTRY, 31337, tmp_path)
    return tmp_path, corpus


def make_answer(corpus, text):
    p = corpus.passages[0]
    ref = PassageRef(p.slug, p.doc_id, p.page, p.index, False)
    return Answer("q", text, True, {"P1": RetrievedPassage("P1", ref, p.text, 0.9)}, ["P1"])


def test_unknown_label_is_unknown_source(published):
    out, corpus = published
    answer = make_answer(corpus, "Claim [P7].")
    assert check_citation("P7", answer, cfg(), out_dir=out).status == "unknown_source"


def test_hash_sent_to_chain_comes_from_displayed_text(published, monkeypatch):
    out, corpus = published
    seen = []
    monkeypatch.setattr(chain, "verify_passage", lambda *a: seen.append(a[-2]) or True)
    answer = make_answer(corpus, "Claim [P1].")

    shown = check_citation("P1", answer, cfg(), out_dir=out, w3=object())
    assert shown.status == "verified" and seen[-1] == corpus.passages[0].content_hash

    check_citation("P1", answer, cfg(), displayed_text="Tampered text", out_dir=out, w3=object())
    assert seen[-1] != corpus.passages[0].content_hash  # the stored hash is never reused


def test_chain_false_means_not_verified(published, monkeypatch):
    out, corpus = published
    monkeypatch.setattr(chain, "verify_passage", lambda *a: False)
    result = check_citation("P1", make_answer(corpus, "Claim [P1]."), cfg(), out_dir=out, w3=object())
    assert result.status == "not_verified" and "\u274c" in result.status_label


def test_unreachable_chain_is_unable_never_verified(published, monkeypatch):
    out, corpus = published

    def down(*a):
        raise ChainError("cannot reach the RPC")

    monkeypatch.setattr(chain, "verify_passage", down)
    result = check_citation("P1", make_answer(corpus, "Claim [P1]."), cfg(), out_dir=out, w3=object())
    assert result.status == "unable" and "\u26aa" in result.status_label


def test_quote_warning_is_added_to_the_result(published, monkeypatch):
    out, corpus = published
    monkeypatch.setattr(chain, "verify_passage", lambda *a: True)
    answer = make_answer(corpus, 'It says "completely invented words" [P1].')
    result = check_citation("P1", answer, cfg(), out_dir=out, w3=object())
    assert result.status == "verified" and result.quote_warnings == ["completely invented words"]


def test_corpus_from_another_registry_or_network_is_unable(published, monkeypatch):
    out, corpus = published
    monkeypatch.setattr(chain, "verify_passage", lambda *a: True)
    answer = make_answer(corpus, "Claim [P1].")
    other = cfg(registry_address="0x70997970C51812dc3A010C7d01b50e0d17dc79C8")
    assert check_citation("P1", answer, other, out_dir=out, w3=object()).status == "unable"
    assert check_citation("P1", answer, cfg(network="glassnet"), out_dir=out, w3=object()).status == "unable"


# --- receipts ----------------------------------------------------------------

GOOD = {
    "corpusId": 1, "docId": "memo", "page": 1, "index": 0,
    "contentHash": "0x" + "ab" * 32, "proof": ["0x" + "cd" * 32],
    "registry": REGISTRY, "chainId": 31337,
}


def test_receipt_for_wrong_chain_id_is_rejected(monkeypatch):
    monkeypatch.setattr(chain, "verify_passage", lambda *a: pytest.fail("must not reach the chain"))
    result = verify_receipt({**GOOD, "chainId": 101001000}, cfg(), w3=object())
    assert result.status == "rejected" and "101001000" in result.reason


def test_receipt_for_wrong_registry_is_rejected(monkeypatch):
    monkeypatch.setattr(chain, "verify_passage", lambda *a: pytest.fail("must not reach the chain"))
    other = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    result = verify_receipt({**GOOD, "registry": other}, cfg(), w3=object())
    assert result.status == "rejected" and "registry" in result.reason


def test_registry_comparison_ignores_address_case(monkeypatch):
    monkeypatch.setattr(chain, "verify_passage", lambda *a: True)
    monkeypatch.setattr(chain, "get_corpus", lambda *a: {"name": "N", "version": "v1"})
    result = verify_receipt({**GOOD, "registry": REGISTRY.lower()}, cfg(), w3=object())
    assert (result.status, result.corpus_name, result.corpus_version) == ("verified", "N", "v1")


@pytest.mark.parametrize("change", [
    {"proof": "nope"}, {"proof": ["0x12"]}, {"contentHash": "0x12"}, {"page": -1},
    {"index": 2**32}, {"corpusId": "1"}, {"docId": ""}, {"registry": "not-an-address"},
    {"chainId": True},
])
def test_malformed_receipts_are_rejected(change):
    assert verify_receipt({**GOOD, **change}, cfg(), w3=object()).status == "rejected"


def test_receipt_missing_fields_or_not_an_object():
    broken = {k: v for k, v in GOOD.items() if k != "proof"}
    assert verify_receipt(broken, cfg(), w3=object()).status == "rejected"
    assert verify_receipt([GOOD], cfg(), w3=object()).status == "rejected"


def test_receipt_unreachable_chain_is_unable(monkeypatch):
    def down(*a):
        raise ChainError("down")

    monkeypatch.setattr(chain, "verify_passage", down)
    assert verify_receipt(GOOD, cfg(), w3=object()).status == "unable"


def test_make_receipt_has_no_text_or_salt_and_requires_authorization(tmp_path):
    corpus = ingest_folder(SAMPLES, "priv", "Priv", "v1", private=True, pattern="private-*")
    save_corpus(corpus, tmp_path)
    record_publication("priv", 1, REGISTRY, 31337, tmp_path)
    p = corpus.passages[0]
    ref = PassageRef("priv", p.doc_id, p.page, p.index, True)
    answer = Answer("q", "x [P1]", True, {"P1": RetrievedPassage("P1", ref, p.text, 0.9)}, ["P1"])

    with pytest.raises(PermissionError):
        verify.make_receipt("P1", answer, cfg(), authorized=False, out_dir=tmp_path)
    receipt = verify.make_receipt("P1", answer, cfg(), authorized=True, out_dir=tmp_path)
    blob = json.dumps(receipt)
    assert p.text[:30] not in blob and p.salt.hex() not in blob and "salt" not in blob
    assert receipt["corpusId"] == 1 and receipt["chainId"] == 31337
    assert receipt["contentHash"] == "0x" + verify.content_hash(p.text, p.salt).hex()
    assert verify._validate_receipt(receipt) is None


# --- end to end on the local Hardhat node (skipped when it is not running) -------

@pytest.fixture
def live():
    config = cfg(private_key=HARDHAT_KEY)
    try:
        chain.connect(config)
    except Exception:
        pytest.skip("no local Hardhat node")
    if not chain.ARTIFACT_PATH.exists():
        pytest.skip("contract not built")
    address = chain.deploy(config)
    return cfg(private_key=HARDHAT_KEY, registry_address=address)


def test_end_to_end_verified_tampered_and_receipt(live, tmp_path, capsys):
    public = ingest_folder(SAMPLES, "pub", "Pub", "v1", pattern="public-*")
    private = ingest_folder(SAMPLES, "priv", "Priv", "v1", private=True, pattern="private-*")
    for corpus in (public, private):
        save_corpus(corpus, tmp_path)
        cid = chain.publish(corpus, live)
        record_publication(corpus.slug, cid, live.registry_address, 31337, tmp_path)

    w3 = chain.connect(live)
    for corpus, authorized in ((public, False), (private, True)):
        p = corpus.passages[1]
        ref = PassageRef(p.slug, p.doc_id, p.page, p.index, corpus.private)
        answer = Answer("q", f'"{p.text[:20]}" [P1]', True, {"P1": RetrievedPassage("P1", ref, p.text, 0.9)}, ["P1"])
        ok = check_citation("P1", answer, live, authorized, w3=w3, out_dir=tmp_path)
        assert ok.status == "verified" and ok.quote_warnings == []
        bad = check_citation("P1", answer, live, authorized, displayed_text=p.text.replace("a", "b", 1), w3=w3, out_dir=tmp_path)
        assert bad.status == "not_verified"

    # private receipt: verified; changed hash: not verified
    receipt = verify.make_receipt("P1", answer, live, True, tmp_path)
    good = verify_receipt(receipt, live, w3)
    assert good.status == "verified" and good.corpus_name == "Priv" and good.corpus_version == "v1"
    forged = copy.deepcopy(receipt)
    forged["contentHash"] = "0x" + "00" * 32
    assert verify_receipt(forged, live, w3).status == "not_verified"

    # CLI path, including a BOM from Windows editors. The CLI uses .env's registry, which is not
    # the registry deployed for this test, so a correct parse ends in a clean "Rejected".
    path = tmp_path / "r.json"
    path.write_text(json.dumps(receipt), encoding="utf-8-sig")
    capsys.readouterr()
    assert main(["verify-receipt", str(path)]) == 1
    captured = capsys.readouterr()
    assert "not valid JSON" not in captured.err and "Rejected" in captured.out
