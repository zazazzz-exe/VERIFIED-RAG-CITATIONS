"""Settings loaded from .env (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

PROVIDERS = ("anthropic", "ollama")

EXPECTED_CHAIN_IDS: dict[str, int] = {"local": 31337, "glassnet": 101001000}


@dataclass(frozen=True)
class Config:
    network: str = "local"
    rpc_url: str = "http://127.0.0.1:8545"
    registry_address: str = ""
    private_key: str = ""
    llm_provider: str = "anthropic"  # answers questions with only public passages
    private_llm_provider: str = "ollama"  # answers questions that include private passages
    anthropic_api_key: str = field(default="", repr=False)  # never print or log this
    anthropic_model: str = ""
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "gemma3:4b"
    top_k: int = 5
    min_similarity: float = 0.35
    auditor_token: str = ""
    explorer_url: str = "https://explorer.glassnet.prismo.network"

    @property
    def expected_chain_id(self) -> int:
        return EXPECTED_CHAIN_IDS[self.network]


def load_config(env_file: Path | None = None) -> Config:
    """Read settings from the environment, loading `.env` first if present."""
    load_dotenv(env_file or ROOT / ".env", encoding="utf-8")

    def get(name: str, default: str) -> str:
        value = os.environ.get(name, "").strip()
        return value if value else default

    network = get("NETWORK", "local").lower()
    if network not in EXPECTED_CHAIN_IDS:
        raise ValueError(
            f"NETWORK must be one of {sorted(EXPECTED_CHAIN_IDS)}, got {network!r}"
        )
    llm_provider = get("LLM_PROVIDER", Config.llm_provider).lower()
    private_llm_provider = get("PRIVATE_LLM_PROVIDER", Config.private_llm_provider).lower()
    for name, value in (("LLM_PROVIDER", llm_provider), ("PRIVATE_LLM_PROVIDER", private_llm_provider)):
        if value not in PROVIDERS:
            raise ValueError(f"{name} must be one of {list(PROVIDERS)}, got {value!r}")
    if private_llm_provider == "anthropic":
        raise ValueError(
            "PRIVATE_LLM_PROVIDER cannot be anthropic: private passages must not be sent to a hosted API"
        )
    return Config(
        network=network,
        rpc_url=get("RPC_URL", Config.rpc_url),
        registry_address=get("REGISTRY_ADDRESS", ""),
        private_key=get("PRIVATE_KEY", ""),
        llm_provider=llm_provider,
        private_llm_provider=private_llm_provider,
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", "").strip(),
        anthropic_model=get("ANTHROPIC_MODEL", ""),
        ollama_url=get("OLLAMA_URL", Config.ollama_url),
        ollama_model=get("OLLAMA_MODEL", Config.ollama_model),
        top_k=int(get("TOP_K", str(Config.top_k))),
        min_similarity=float(get("MIN_SIMILARITY", str(Config.min_similarity))),
        auditor_token=os.environ.get("AUDITOR_TOKEN", "").strip(),
        explorer_url=get("EXPLORER_URL", Config.explorer_url),
    )
