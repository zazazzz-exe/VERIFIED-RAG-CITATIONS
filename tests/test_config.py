import pytest

from vrag.config import Config, load_config

ENV_NAMES = [
    "NETWORK", "RPC_URL", "REGISTRY_ADDRESS", "PRIVATE_KEY", "LLM_PROVIDER",
    "PRIVATE_LLM_PROVIDER", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "OLLAMA_URL",
    "OLLAMA_MODEL", "TOP_K", "MIN_SIMILARITY", "AUDITOR_TOKEN", "EXPLORER_URL",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    return tmp_path / "missing.env"  # a path with no .env file


def test_defaults(clean_env):
    c = load_config(clean_env)
    assert (c.llm_provider, c.private_llm_provider) == ("anthropic", "ollama")
    assert c.ollama_model == "gemma3:4b"
    assert c.anthropic_api_key == "" and c.anthropic_model == ""
    assert c.expected_chain_id == 31337


def test_reads_claude_settings(clean_env, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-opus-5-5")
    c = load_config(clean_env)
    assert c.anthropic_api_key == "sk-ant-secret" and c.anthropic_model == "claude-opus-5-5"


def test_api_key_is_not_in_repr(clean_env, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    c = load_config(clean_env)
    assert "sk-ant-secret" not in repr(c) and "sk-ant-secret" not in str(c)


def test_private_provider_can_never_be_anthropic(clean_env, monkeypatch):
    monkeypatch.setenv("PRIVATE_LLM_PROVIDER", "anthropic")
    with pytest.raises(ValueError, match="private"):
        load_config(clean_env)


def test_unknown_provider_rejected(clean_env, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        load_config(clean_env)


def test_public_provider_can_be_ollama(clean_env, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    assert load_config(clean_env).llm_provider == "ollama"


def test_unknown_network_rejected(clean_env, monkeypatch):
    monkeypatch.setenv("NETWORK", "mainnet")
    with pytest.raises(ValueError):
        load_config(clean_env)


def test_config_has_no_embedding_setting():
    assert not hasattr(Config(), "ollama_embed_model")
