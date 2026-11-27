"""Streamlit web app: ask questions and inspect verified citations.

Run from the repo root with:  streamlit run app\\streamlit_app.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# `streamlit run` puts this folder on the path, not the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

from vrag import chain, rag, verify  # noqa: E402
from vrag.chain import ChainError, WrongChainError  # noqa: E402
from vrag.config import check_auditor_token, load_config  # noqa: E402
from vrag.rag import LLMError  # noqa: E402
from vrag.store import OUT_DIR, load_corpus  # noqa: E402

PROVIDER_CHOICES = {
    "Auto (Claude for public, Ollama for private)": None,
    "Ollama (compare, public or private)": "ollama",
    "Claude (public passages only)": "anthropic",
}
TAMPER_NOTE = "(modified for demo)"


def escape_markdown(text: str) -> str:
    """Keep `$` amounts from being rendered as math."""
    return text.replace("$", "\\$")


@st.cache_resource
def get_w3(rpc_url: str, network: str):
    """One connection per process. Raises (and is not cached) when the chain ID is wrong."""
    return chain.connect(load_config())


def registry_link(config) -> str:
    if config.network == "glassnet" and config.registry_address:
        return f"[{config.registry_address}]({chain.address_url(config, config.registry_address)})"
    return f"`{config.registry_address or 'not set'}`"


def render_sidebar(config, chain_id: int | None) -> tuple[bool, str | None, bool]:
    """Sidebar controls. Returns (authorized, provider override, tamper demo on)."""
    st.sidebar.header("Network")
    if chain_id is None:
        st.sidebar.warning(f"Chain unreachable at {config.rpc_url}. Citations will show ⚪.")
    st.sidebar.markdown(
        f"**Network:** {config.network}  \n"
        f"**Chain ID:** {chain_id if chain_id is not None else 'unknown'} "
        f"(expected {config.expected_chain_id})  \n"
        f"**Registry:** {registry_link(config)}"
    )

    st.sidebar.header("Private documents")
    if st.session_state.get("authorized"):
        st.sidebar.success("Auditor unlocked")
        if st.sidebar.button("Lock", key="lock"):
            st.session_state["authorized"] = False
            current = st.session_state.get("current")
            if current and any(p.ref.private for p in current["answer"].passages.values()):
                st.session_state["current"] = None  # do not keep private text after locking
            st.rerun()
    elif not config.auditor_token:
        st.sidebar.caption("Disabled: AUDITOR_TOKEN is not set in .env.")
    else:
        with st.sidebar.form("unlock", clear_on_submit=True):
            token = st.text_input("Auditor token", type="password", key="token_input")
            if st.form_submit_button("Unlock"):
                if check_auditor_token(token, config):
                    st.session_state["authorized"] = True
                    st.rerun()
                time.sleep(1)  # slow down guessing
                st.error("Wrong token.")

    st.sidebar.header("Answer model")
    choice = st.sidebar.selectbox("Provider", list(PROVIDER_CHOICES), key="provider")
    st.sidebar.header("Demo")
    tamper = st.sidebar.toggle(
        "Tamper demo", key="tamper", help="Changes one character of the first cited passage before verification."
    )
    return bool(st.session_state.get("authorized")), PROVIDER_CHOICES[choice], tamper


def run_question(question: str, authorized: bool, provider: str | None, config, out_dir: Path) -> None:
    try:
        with st.spinner("Searching and asking the model..."):
            answer = rag.ask(question, authorized, provider, config, out_dir)
    except (LLMError, ValueError, FileNotFoundError) as exc:
        st.session_state["current"] = None
        st.session_state["error"] = str(exc)
        return
    st.session_state["error"] = None
    st.session_state["serial"] = st.session_state.get("serial", 0) + 1
    st.session_state["current"] = {"answer": answer, "id": st.session_state["serial"], "question": question}


def get_results(current, config, authorized, tamper_on, w3, out_dir):
    """Verify citations; cached per (answer, tamper, authorization) so reruns are cheap."""
    answer = current["answer"]
    displayed: dict[str, str] = {}
    if tamper_on:
        first = next((label for label in answer.citations if label in answer.passages), None)
        if first:
            displayed[first] = verify.tamper(answer.passages[first].text)
    key = (current["id"], tamper_on, authorized)
    cached = st.session_state.get("results_cache")
    if cached and cached[0] == key:
        return cached[1], displayed
    results = verify.check_answer(answer, config, authorized, displayed, w3, out_dir)
    st.session_state["results_cache"] = (key, results)
    return results, displayed


def render_answer(current, config, authorized, tamper_on, w3, out_dir: Path) -> None:
    answer = current["answer"]
    with st.chat_message("user"):
        st.write(current["question"])
    with st.chat_message("assistant"):
        if not answer.found:
            st.info(answer.text)
            return
        st.markdown(escape_markdown(answer.text))
        st.caption(f"Answered by {answer.model} via {answer.provider}")

        results, displayed = get_results(current, config, authorized, tamper_on, w3, out_dir)
        st.subheader("Citations")
        if not results:
            st.write("The answer cites no passages.")
        for result in results:
            render_citation(result, answer, displayed, config, authorized, out_dir)


def render_citation(result, answer, displayed, config, authorized, out_dir: Path) -> None:
    passage = answer.passages.get(result.label)
    with st.expander(f"[{result.label}] {result.status_label}", expanded=False):
        if passage is None:
            st.write("The answer cited a passage that was not among the retrieved passages.")
            return
        modified = result.label in displayed
        text = displayed.get(result.label, passage.text)
        st.markdown(f"**Passage {passage.ref.passage_id}** {TAMPER_NOTE if modified else ''}")
        st.text(text)
        try:
            corpus = load_corpus(passage.ref.slug, out_dir)
            st.write(f"Corpus: {corpus.name} ({corpus.version}), corpus ID {corpus.corpus_id}")
        except FileNotFoundError:
            st.write(f"Corpus: {passage.ref.slug}")
        st.markdown(f"Registry contract: {registry_link(config)}")
        if result.detail:
            st.caption(result.detail)
        for quote in result.quote_warnings:
            st.warning(f'{verify.QUOTE_WARNING}: "{quote}"')
        if authorized and passage.ref.private and result.status == "verified":
            receipt = verify.make_receipt(result.label, answer, config, authorized, out_dir)
            st.download_button(
                "Export receipt",
                data=json.dumps(receipt, indent=2),
                file_name=f"receipt-{passage.ref.slug}-{result.label}.json",
                mime="application/json",
                key=f"receipt-{result.label}",
            )


def render_receipt_verifier(config, w3) -> None:
    with st.expander("Verify a citation receipt"):
        st.caption("Paste a receipt. It is checked on-chain; no passage text is needed.")
        raw = st.text_area("Receipt JSON", key="receipt_input", height=200)
        if st.button("Verify receipt", key="verify_receipt"):
            try:
                receipt = json.loads(raw)
            except json.JSONDecodeError as exc:
                st.error(f"Not valid JSON: {exc}")
                return
            result = verify.verify_receipt(receipt, config, w3)
            if result.status == "verified":
                st.success(verify.STATUS_LABELS["verified"])
            elif result.status == "unable":
                st.warning(verify.STATUS_LABELS["unable"])
            else:
                st.error("\u274c Rejected" if result.status == "rejected" else verify.STATUS_LABELS["not_verified"])
            if result.corpus_name:
                st.write(f"Corpus: {result.corpus_name} ({result.corpus_version})")
            if result.reason:
                st.write(result.reason)


def main() -> None:
    st.set_page_config(page_title="Verifiable RAG citations", page_icon="\u2705")
    st.title("Verifiable RAG citations")
    st.caption("Answers from official documents. Every citation is checked against a Merkle root on Prismo.")

    config = load_config()
    out_dir = Path(os.environ.get("VRAG_OUT_DIR") or OUT_DIR)
    try:
        w3 = get_w3(config.rpc_url, config.network)
    except WrongChainError as exc:
        st.error(f"Refusing to run: {exc}")
        st.stop()
    except ChainError:
        w3 = None  # unreachable chain: citations will show "Unable to verify"

    authorized, provider, tamper_on = render_sidebar(config, w3.eth.chain_id if w3 else None)

    if st.session_state.get("error"):
        st.error(st.session_state["error"])
    current = st.session_state.get("current")
    if current:
        render_answer(current, config, authorized, tamper_on, w3, out_dir)

    question = st.chat_input("Ask a question about the documents")
    if question:
        run_question(question, authorized, provider, config, out_dir)
        st.rerun()

    render_receipt_verifier(config, w3)


main()
