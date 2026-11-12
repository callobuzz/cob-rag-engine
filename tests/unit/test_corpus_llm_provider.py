"""A corpus can choose its own LLM provider (and fast model) through the API."""

from callobuzz_rag.api.schemas.corpus import LLMConfigInput
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.config_resolver import ConfigResolver


def test_schema_accepts_provider_and_fast_model() -> None:
    cfg = LLMConfigInput(provider="ollama", model="qwen2.5:0.5b", fast_model="qwen2.5:0.5b")
    assert cfg.model_dump(exclude_none=True) == {
        "provider": "ollama",
        "model": "qwen2.5:0.5b",
        "fast_model": "qwen2.5:0.5b",
    }


def test_corpus_provider_overrides_engine_default() -> None:
    settings = Settings(_env_file=None, llm_provider="openrouter", llm_model="openai/gpt-oss-20b")
    stored = LLMConfigInput(
        provider="ollama", model="qwen2.5:0.5b", base_url="http://127.0.0.1:11434"
    ).model_dump(exclude_none=True)

    resolved = ConfigResolver(settings).resolve_llm(request_headers=None, corpus_llm_config=stored)

    assert resolved.provider == "ollama"
    assert resolved.model == "qwen2.5:0.5b"
    assert resolved.base_url == "http://127.0.0.1:11434"
