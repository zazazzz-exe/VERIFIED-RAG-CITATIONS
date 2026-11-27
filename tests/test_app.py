import json
from types import SimpleNamespace

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from vrag import chain, rag, verify
from vrag.chain import WrongChainError
from vrag.config import ROOT
from vrag.index import index_corpus
from vrag.ingest import ingest_folder
from vrag.store import record_publication, save_corpus
from vrag.text import content_hash

APP = str(ROOT / "app" / "streamlit_app.py")
SAMPLES = ROOT / "data" / "samples"
REGISTRY = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
MERKLE_Q = "What is a Merkle proof and how big is it?"
PRIVATE_Q = "How much is allocated to the Riverside Footbridge?"


@pytest.fixture(scope="module")
def out_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("out")
    for pattern, slug, private, cid in (("public-*", "pub", False, 0), ("private-*", "priv", True, 1)):
        corpus = ingest_folder(SAMPLES, slug, slug.title(), "v1", private=private, pattern=pattern)
        save_corpus(corpus, out)
        record_publication(slug, cid, REGISTRY, 31337, out)
        index_corpus(slug, out)
    return out


@pytest.fixture
def app(out_dir, monkeypatch):
    """The app with fake chain and fake LLMs; a passage verifies only if its hash is unchanged."""
    st.cache_resource.clear()
    expected = {}
    for slug, private in (("pub", False), ("priv", True)):
        from vrag.store import load_corpus

        for p in load_corpus(slug, out_dir).passages:
            expected[(p.doc_id, p.page, p.index)] = p.content_hash

    monkeypatch.setenv("VRAG_OUT_DIR", str(out_dir))
    monkeypatch.setenv("NETWORK", "local")
    monkeypatch.setenv("REGISTRY_ADDRESS", REGISTRY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-test")
    monkeypatch.setenv("AUDITOR_TOKEN", "")
    fake_w3 = SimpleNamespace(eth=SimpleNamespace(chain_id=31337))
    monkeypatch.setattr(chain, "connect", lambda config: fake_w3)
    monkeypatch.setattr(
        chain, "verify_passage",
        lambda config, cid, proof, doc, page, idx, chash, w3=None: expected[(doc, page, idx)] == chash,
    )
    monkeypatch.setattr(chain, "get_corpus", lambda config, cid, w3=None: {"name": "Priv", "version": "v1"})
    state = SimpleNamespace(prompts=[])

    def claude(config, system, prompt):
        state.prompts.append(("claude", prompt))
        return 'A Merkle proof shows "that one leaf belongs to a tree with a known root" [P1]. Also [P9].'

    def ollama(config, system, prompt):
        state.prompts.append(("ollama", prompt))
        return "The allocation is 1,250,000 units [P1]."

    monkeypatch.setattr(rag, "_call_anthropic", claude)
    monkeypatch.setattr(rag, "_call_ollama", ollama)
    at = AppTest.from_file(APP, default_timeout=120)
    at.state = state
    return at


def ask(at, question):
    at.chat_input[0].set_value(question).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def expander_labels(at):
    return [e.label for e in at.expander]


def sidebar_text(at):
    return " ".join(m.value for m in at.sidebar.markdown)


def test_shows_network_and_chain_id(app):
    app.run()
    assert not app.exception
    text = sidebar_text(app)
    assert "local" in text and "31337" in text and REGISTRY in text


def test_private_features_are_disabled_without_a_token(app):
    app.run()
    assert any("AUDITOR_TOKEN is not set" in c.value for c in app.sidebar.caption)
    assert len(app.sidebar.text_input) == 0


def test_answer_with_verified_and_unknown_citations(app):
    ask(app.run(), MERKLE_Q)
    labels = expander_labels(app)
    assert any(l.startswith("[P1]") and "\u2705 Verified" in l for l in labels), labels
    assert any(l.startswith("[P9]") and "\u274c Unknown source" in l for l in labels), labels
    assert any("claude-test via anthropic" in c.value for c in app.caption)
    assert not any("Quote not found" in w.value for w in app.warning)


def test_tamper_demo_breaks_only_the_first_citation(app):
    ask(app.run(), MERKLE_Q)
    app.sidebar.toggle(key="tamper").set_value(True).run()
    assert not app.exception
    labels = expander_labels(app)
    assert any(l.startswith("[P1]") and "\u274c Not verified" in l for l in labels), labels
    assert any("(modified for demo)" in m.value for m in app.markdown)
    app.sidebar.toggle(key="tamper").set_value(False).run()
    assert any(l.startswith("[P1]") and "\u2705 Verified" in l for l in expander_labels(app))


def test_tamper_does_not_ask_the_model_again(app):
    ask(app.run(), MERKLE_Q)
    app.sidebar.toggle(key="tamper").set_value(True).run()
    assert len(app.state.prompts) == 1


def test_question_with_no_relevant_passage(app):
    ask(app.run(), "What is the capital of France?")
    assert any("could not find" in i.value for i in app.info)
    assert app.state.prompts == []


def test_wrong_chain_stops_the_app(app, monkeypatch):
    def wrong(config):
        raise WrongChainError("NETWORK=local expects chain ID 31337, but the RPC reports chain ID 1")

    monkeypatch.setattr(chain, "connect", wrong)
    app.run()
    assert any("Refusing to run" in e.value for e in app.error)
    assert len(app.chat_input) == 0


def test_unreachable_chain_shows_unable_not_verified(app, monkeypatch):
    def down(*a, **k):
        raise chain.ChainError("down")

    monkeypatch.setattr(chain, "connect", down)
    monkeypatch.setattr(chain, "verify_passage", down)
    ask(app.run(), MERKLE_Q)
    assert any(l.startswith("[P1]") and "\u26aa Unable to verify" in l for l in expander_labels(app))
    assert any("unreachable" in w.value for w in app.sidebar.warning)


# --- auditor unlock -----------------------------------------------------------

def unlock(app, token):
    app.sidebar.text_input(key="token_input").set_value(token)
    app.sidebar.button[0].click().run()


def test_wrong_token_does_not_unlock(app, monkeypatch):
    monkeypatch.setenv("AUDITOR_TOKEN", "s3cret-phrase")
    monkeypatch.setattr("time.sleep", lambda s: None)
    app.run()
    unlock(app, "wrong")
    assert any("Wrong token" in e.value for e in app.sidebar.error)
    ask(app, PRIVATE_Q)
    assert any("could not find" in i.value for i in app.info)  # private text stays hidden
    assert app.state.prompts == []


def test_correct_token_unlocks_private_answers_via_ollama(app, monkeypatch):
    monkeypatch.setenv("AUDITOR_TOKEN", "s3cret-phrase")
    app.run()
    unlock(app, "s3cret-phrase")
    assert any("unlocked" in s.value for s in app.sidebar.success)
    ask(app, PRIVATE_Q)
    assert [name for name, _ in app.state.prompts] == ["ollama"]  # never Claude
    assert any(l.startswith("[P1]") and "\u2705 Verified" in l for l in expander_labels(app))
    assert any("gemma3:4b via ollama" in c.value for c in app.caption)


def test_locking_discards_a_private_answer(app, monkeypatch):
    monkeypatch.setenv("AUDITOR_TOKEN", "s3cret-phrase")
    app.run()
    unlock(app, "s3cret-phrase")
    ask(app, PRIVATE_Q)
    assert expander_labels(app)
    app.sidebar.button(key="lock").click().run()
    assert not app.exception
    assert not any(l.startswith("[P1]") for l in expander_labels(app))
    assert not any("1,250,000" in m.value for m in app.markdown)


def test_claude_choice_on_private_question_shows_an_error(app, monkeypatch):
    monkeypatch.setenv("AUDITOR_TOKEN", "s3cret-phrase")
    app.run()
    unlock(app, "s3cret-phrase")
    app.sidebar.selectbox(key="provider").select("Claude (public passages only)").run()
    ask(app, PRIVATE_Q)
    assert any("refusing to send private passages" in e.value for e in app.error)
    assert app.state.prompts == []


# --- receipts -------------------------------------------------------------------

def test_receipt_verifier_accepts_and_rejects(app, monkeypatch):
    app.run()
    good = {
        "corpusId": 1, "docId": "private-synthetic-memo", "page": 1, "index": 0,
        "contentHash": "0x" + "ab" * 32, "proof": [], "registry": REGISTRY, "chainId": 31337,
    }
    monkey_expected = {("private-synthetic-memo", 1, 0): bytes.fromhex("ab" * 32)}
    monkeypatch.setattr(
        chain, "verify_passage",
        lambda config, cid, proof, doc, page, idx, chash, w3=None: monkey_expected[(doc, page, idx)] == chash,
    )
    app.text_area(key="receipt_input").set_value(json.dumps(good))
    app.button(key="verify_receipt").click().run()
    assert any("Verified" in s.value for s in app.success)
    assert any("Priv (v1)" in m.value for m in app.markdown)

    app.text_area(key="receipt_input").set_value(json.dumps({**good, "chainId": 101001000}))
    app.button(key="verify_receipt").click().run()
    assert any("Rejected" in e.value for e in app.error)

    app.text_area(key="receipt_input").set_value("not json")
    app.button(key="verify_receipt").click().run()
    assert any("Not valid JSON" in e.value for e in app.error)


# --- helper ---------------------------------------------------------------------

def test_tamper_changes_one_character():
    assert verify.tamper("Budget 2026 total") == "Budget 3026 total"
    assert verify.tamper("Budget 9") == "Budget 0"
    assert verify.tamper("no digits") == "Xo digits"
    assert verify.tamper("Xylophone") == "Yylophone"
    assert verify.tamper("") == ""
    for text in ("a1b", "plain", "X"):
        changed = verify.tamper(text)
        assert changed != text and len(changed) == len(text)
        assert content_hash(changed) != content_hash(text)
