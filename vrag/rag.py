"""Retrieve passages, ask an LLM, and parse its `[P#]` citations.

Provider routing: answers built only from public passages use `LLM_PROVIDER` (Claude by
default). Any private passage forces `PRIVATE_LLM_PROVIDER` (Ollama). Private text is never
sent to the Anthropic API, and that is enforced again right before the API call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import anthropic
import requests

from vrag import index
from vrag.config import Config, load_config
from vrag.index import PassageRef
from vrag.store import OUT_DIR, get_passage

NOT_FOUND_TEXT = "I could not find the answer in the documents."

SYSTEM_PROMPT = (
    "You answer questions using ONLY the numbered passages provided by the user.\n"
    "Rules:\n"
    "- Use only information that appears in the passages. Do not use outside knowledge.\n"
    "- After every claim, cite the passage it came from with its label, for example [P1]. "
    "Use only the labels you were given.\n"
    "- When you quote a passage, copy the exact words, put them in double quotes, and put "
    'the label right after the closing quote, for example "exact words" [P1].\n'
    "- If the passages do not answer the question, reply exactly: The documents do not say."
)

# A bracket group such as [P1] or [P1, P2]; the numbers inside are captured separately.
_BRACKET = re.compile(r"\[\s*P\d+(?:\s*,\s*P?\d+)*\s*\]")
_NUMBER = re.compile(r"\d+")


class LLMError(RuntimeError):
    """The language model could not be used (not configured, unreachable, or refused)."""


class PrivateDataError(LLMError):
    """A private passage would have been sent to a hosted API."""


@dataclass(frozen=True)
class RetrievedPassage:
    label: str  # "P1", "P2", ...
    ref: PassageRef
    text: str
    similarity: float


@dataclass
class Answer:
    question: str
    text: str
    found: bool  # False when nothing relevant was retrieved (no LLM call was made)
    passages: dict[str, RetrievedPassage] = field(default_factory=dict)
    citations: list[str] = field(default_factory=list)  # labels in order of first use
    provider: str | None = None
    model: str | None = None


def parse_citations(text: str) -> list[str]:
    """Labels cited in `text`, in order of first appearance, e.g. ["P1", "P3"]."""
    labels: list[str] = []
    for bracket in _BRACKET.finditer(text):
        for number in _NUMBER.findall(bracket.group()):
            label = f"P{int(number)}"
            if label not in labels:
                labels.append(label)
    return labels


def choose_provider(has_private: bool, config: Config, override: str | None = None) -> str:
    """Pick the LLM provider for a question. Never returns `anthropic` for private data."""
    if override is not None and override not in ("anthropic", "ollama"):
        raise ValueError(f"unknown provider {override!r}; use 'anthropic' or 'ollama'")
    if has_private:
        if override == "anthropic":
            raise PrivateDataError(
                "refusing to send private passages to the Anthropic API; "
                "use --provider ollama or leave the provider unset"
            )
        provider = override or config.private_llm_provider
        if provider == "anthropic":  # config validation already forbids this
            raise PrivateDataError("PRIVATE_LLM_PROVIDER cannot be anthropic")
        return provider
    return override or config.llm_provider


def build_prompt(question: str, passages: list[RetrievedPassage]) -> str:
    blocks = "\n\n".join(f"[{p.label}] {p.text}" for p in passages)
    return f"Passages:\n\n{blocks}\n\nQuestion: {question}"


def _call_anthropic(config: Config, system: str, prompt: str) -> str:
    """One Claude request. No sampling parameters: claude-opus-5-5 rejects `temperature`."""
    if not config.anthropic_api_key:
        raise LLMError("Claude is not configured: set ANTHROPIC_API_KEY in .env")
    if not config.anthropic_model:
        raise LLMError("Claude is not configured: set ANTHROPIC_MODEL in .env")
    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    try:
        response = client.messages.create(
            model=config.anthropic_model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError:
        raise LLMError("Claude rejected the API key: check ANTHROPIC_API_KEY in .env") from None
    except anthropic.NotFoundError:
        raise LLMError(
            f"Claude model {config.anthropic_model!r} was not found: check ANTHROPIC_MODEL in .env"
        ) from None
    except anthropic.APIConnectionError:
        raise LLMError("cannot reach the Anthropic API: check your internet connection") from None
    except anthropic.APIError as exc:
        message = str(exc).replace(config.anthropic_api_key, "<key>")
        raise LLMError(f"Claude request failed: {message}") from None
    return "".join(block.text for block in response.content if block.type == "text").strip()


def _call_ollama(config: Config, system: str, prompt: str) -> str:
    url = config.ollama_url.rstrip("/") + "/api/chat"
    body = {
        "model": config.ollama_model,
        "stream": False,
        "options": {"temperature": 0},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    }
    try:
        response = requests.post(url, json=body, timeout=300)
    except requests.RequestException as exc:
        raise LLMError(
            f"Ollama is not reachable at {config.ollama_url} (OLLAMA_URL in .env). "
            f"Start Ollama and try again ({type(exc).__name__})"
        ) from None
    if response.status_code == 404:
        raise LLMError(
            f"Ollama model {config.ollama_model!r} is not installed: "
            f"run `ollama pull {config.ollama_model}`"
        )
    if not response.ok:
        raise LLMError(f"Ollama returned HTTP {response.status_code}: {response.text[:200]}")
    return response.json()["message"]["content"].strip()


def generate(
    provider: str, config: Config, question: str, passages: list[RetrievedPassage]
) -> tuple[str, str]:
    """Call the provider. Returns (answer text, model name)."""
    # Final guard, independent of routing: private text never reaches the Anthropic API.
    if provider == "anthropic" and any(p.ref.private for p in passages):
        raise PrivateDataError("refusing to send private passages to the Anthropic API")
    prompt = build_prompt(question, passages)
    if provider == "anthropic":
        return _call_anthropic(config, SYSTEM_PROMPT, prompt), config.anthropic_model
    if provider == "ollama":
        return _call_ollama(config, SYSTEM_PROMPT, prompt), config.ollama_model
    raise ValueError(f"unknown provider {provider!r}")


def ask(
    question: str,
    authorized: bool = False,
    provider: str | None = None,
    config: Config | None = None,
    out_dir: Path = OUT_DIR,
) -> Answer:
    """Search, then answer from the retrieved passages and parse the citations."""
    config = config or load_config()
    hits = index.search(question, config.top_k, authorized, out_dir)
    relevant = [(ref, score) for ref, score in hits if score >= config.min_similarity]
    if not relevant:  # nothing relevant enough: do not call the LLM
        return Answer(question=question, text=NOT_FOUND_TEXT, found=False)

    passages: list[RetrievedPassage] = []
    for ref, score in relevant:
        passage = get_passage(ref.slug, ref.doc_id, ref.page, ref.index, authorized, out_dir)
        if passage is None or passage.text is None:
            continue  # private text hidden from this session
        passages.append(RetrievedPassage(f"P{len(passages) + 1}", ref, passage.text, score))
    if not passages:
        return Answer(question=question, text=NOT_FOUND_TEXT, found=False)

    chosen = choose_provider(any(p.ref.private for p in passages), config, provider)
    text, model = generate(chosen, config, question, passages)
    return Answer(
        question=question,
        text=text,
        found=True,
        passages={p.label: p for p in passages},
        citations=parse_citations(text),
        provider=chosen,
        model=model,
    )
