"""Tests for the 3-tier config resolution chain (request -> corpus -> defaults)."""

from dataclasses import fields
from unittest.mock import MagicMock


def _make_settings() -> MagicMock:
    """Create a mock Settings with LLM defaults."""
    settings = MagicMock()
    settings.llm_model = "anthropic/claude-sonnet-4-20250514"
    settings.llm_api_key = MagicMock()
    settings.llm_api_key.get_secret_value.return_value = "sk-default-engine-key"
    settings.llm_api_base = None
    settings.llm_temperature = 0.7
    settings.llm_max_tokens = 2048
    return settings


def _make_encryptor() -> MagicMock:
    """Create a mock FernetEncryptor."""
    encryptor = MagicMock()
    encryptor.decrypt.return_value = "sk-decrypted-corpus-key"
    return encryptor


class TestResolvedLLMConfig:
    def test_dataclass_fields(self) -> None:
        from callobuzz_rag.core.config_resolver import ResolvedLLMConfig

        field_names = {f.name for f in fields(ResolvedLLMConfig)}
        assert field_names == {
            "model",
            "api_key",
            "base_url",
            "temperature",
            "max_tokens",
            "fast_model",
            # OpenRouter hosts to try first for the fast model (not per request).
            "fast_provider_order",
            # Sent as X-LLM-Provider. Passed to LiteLLM as custom_llm_provider so
            # the caller's provider, model and key always agree.
            "provider",
            # Engine settings for query understanding, not per request.
            "fast_temperature",
            "fast_max_tokens",
            "timeout",
            "fast_timeout",
            # Thinking effort: corpus llm_config, else RAG_LLM_REASONING_EFFORT.
            "thinking",
            "fast_thinking",
            # Whose key pays: the engine's own ("operator") or a stored / sent one ("byok").
            "payer",
            # Answer model per reply language (RAG_LLM_LANGUAGE_MODELS, llm_config.language_models).
            "language_models",
        }

    def test_construction(self) -> None:
        from callobuzz_rag.core.config_resolver import ResolvedLLMConfig

        config = ResolvedLLMConfig(
            model="anthropic/claude-sonnet-4-20250514",
            api_key="sk-test-123",
            base_url=None,
            temperature=0.7,
            max_tokens=2048,
        )
        assert config.model == "anthropic/claude-sonnet-4-20250514"
        assert config.api_key == "sk-test-123"
        assert config.base_url is None
        assert config.temperature == 0.7
        assert config.max_tokens == 2048

    def test_base_url_nullable(self) -> None:
        from callobuzz_rag.core.config_resolver import ResolvedLLMConfig

        config = ResolvedLLMConfig(
            model="m",
            api_key="k",
            base_url="http://localhost:11434",
            temperature=0.5,
            max_tokens=1024,
        )
        assert config.base_url == "http://localhost:11434"


class TestResolvedPromptConfig:
    def test_dataclass_fields(self) -> None:
        from callobuzz_rag.core.config_resolver import ResolvedPromptConfig

        field_names = {f.name for f in fields(ResolvedPromptConfig)}
        assert field_names == {"system_prompt", "constraints", "response_language"}

    def test_construction(self) -> None:
        from callobuzz_rag.core.config_resolver import ResolvedPromptConfig

        config = ResolvedPromptConfig(
            system_prompt="You are helpful.",
            constraints="Only use context.",
            response_language=None,
        )
        assert config.system_prompt == "You are helpful."
        assert config.constraints == "Only use context."
        assert config.response_language is None

    def test_response_language_nullable(self) -> None:
        from callobuzz_rag.core.config_resolver import ResolvedPromptConfig

        config = ResolvedPromptConfig(
            system_prompt="s",
            constraints="c",
            response_language="es",
        )
        assert config.response_language == "es"


class TestResolveLLM:
    def test_the_engine_key_is_paid_by_the_operator(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver

        assert ConfigResolver(_make_settings()).resolve_llm().payer == "operator"

    def test_a_request_key_or_stored_key_is_byok(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver

        resolver = ConfigResolver(_make_settings(), encryptor=_make_encryptor())
        sent = resolver.resolve_llm(request_headers={"X-LLM-API-Key": "sk-theirs"})
        stored = resolver.resolve_llm(corpus_llm_config={"api_key_encrypted": "blob"})
        assert sent.payer == "byok" and stored.payer == "byok"

    def test_llm_defaults_from_settings(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver

        settings = _make_settings()
        resolver = ConfigResolver(settings)
        result = resolver.resolve_llm()

        assert result.model == "anthropic/claude-sonnet-4-20250514"
        assert result.api_key == "sk-default-engine-key"
        assert result.base_url is None
        assert result.temperature == 0.7
        assert result.max_tokens == 2048

    def test_llm_corpus_overrides_settings(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver

        settings = _make_settings()
        encryptor = _make_encryptor()
        resolver = ConfigResolver(settings, encryptor=encryptor)

        corpus_config = {
            "model": "openai/gpt-4o",
            "api_key_encrypted": "encrypted_blob_here",
            "base_url": "http://localhost:11434",
            "temperature": 0.3,
            "max_tokens": 4096,
        }

        result = resolver.resolve_llm(corpus_llm_config=corpus_config)

        assert result.model == "openai/gpt-4o"
        assert result.api_key == "sk-decrypted-corpus-key"
        assert result.base_url == "http://localhost:11434"
        assert result.temperature == 0.3
        assert result.max_tokens == 4096

    def test_llm_request_header_overrides_corpus(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver

        settings = _make_settings()
        encryptor = _make_encryptor()
        resolver = ConfigResolver(settings, encryptor=encryptor)

        corpus_config = {
            "model": "openai/gpt-4o",
            "api_key_encrypted": "encrypted_blob",
            "temperature": 0.3,
            "max_tokens": 4096,
        }

        headers = {
            "x-llm-model": "google/gemini-pro",
            "x-llm-api-key": "sk-request-override-key",
            "x-llm-base-url": "http://custom-proxy:8080",
        }

        result = resolver.resolve_llm(
            request_headers=headers,
            corpus_llm_config=corpus_config,
        )

        assert result.model == "google/gemini-pro"
        assert result.api_key == "sk-request-override-key"
        assert result.base_url == "http://custom-proxy:8080"
        assert result.temperature == 0.3
        assert result.max_tokens == 4096

    def test_llm_partial_corpus_override(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver

        settings = _make_settings()
        resolver = ConfigResolver(settings)

        corpus_config = {"model": "openai/gpt-4o-mini"}

        result = resolver.resolve_llm(corpus_llm_config=corpus_config)

        assert result.model == "openai/gpt-4o-mini"
        assert result.api_key == "sk-default-engine-key"
        assert result.base_url is None
        assert result.temperature == 0.7
        assert result.max_tokens == 2048

    def test_llm_corpus_encrypted_key_decrypted(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver

        settings = _make_settings()
        encryptor = _make_encryptor()
        encryptor.decrypt.return_value = "sk-my-secret-corpus-key"
        resolver = ConfigResolver(settings, encryptor=encryptor)

        corpus_config = {"api_key_encrypted": "gAAAAB_encrypted_payload"}

        result = resolver.resolve_llm(corpus_llm_config=corpus_config)

        encryptor.decrypt.assert_called_once_with("gAAAAB_encrypted_payload")
        assert result.api_key == "sk-my-secret-corpus-key"

    def test_llm_request_header_key_overrides_corpus_key(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver

        settings = _make_settings()
        encryptor = _make_encryptor()
        resolver = ConfigResolver(settings, encryptor=encryptor)

        corpus_config = {"api_key_encrypted": "encrypted_corpus_key"}
        headers = {"x-llm-api-key": "sk-from-request-header"}

        result = resolver.resolve_llm(
            request_headers=headers,
            corpus_llm_config=corpus_config,
        )

        assert result.api_key == "sk-from-request-header"
        encryptor.decrypt.assert_not_called()

    def test_llm_no_key_anywhere_uses_default(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver

        settings = _make_settings()
        resolver = ConfigResolver(settings)

        result = resolver.resolve_llm(request_headers={}, corpus_llm_config={})

        assert result.api_key == "sk-default-engine-key"

    def test_llm_decryption_failure_falls_through(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver

        settings = _make_settings()
        encryptor = _make_encryptor()
        encryptor.decrypt.side_effect = Exception("Invalid Fernet token")
        resolver = ConfigResolver(settings, encryptor=encryptor)

        corpus_config = {"api_key_encrypted": "corrupted_blob"}

        result = resolver.resolve_llm(corpus_llm_config=corpus_config)

        assert result.api_key == "sk-default-engine-key"


class TestResolvePrompt:
    def test_prompt_defaults_from_templates(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver
        from callobuzz_rag.retrieval.prompt_templates import CONSTRAINTS_BLOCK, SYSTEM_PROMPT

        settings = _make_settings()
        resolver = ConfigResolver(settings)
        result = resolver.resolve_prompt()

        assert result.system_prompt == SYSTEM_PROMPT
        assert result.constraints == CONSTRAINTS_BLOCK
        assert result.response_language is None

    def test_prompt_corpus_overrides_default(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver

        settings = _make_settings()
        resolver = ConfigResolver(settings)

        corpus_config = {
            "system_prompt": "You are a legal expert assistant.",
            "constraints": "Only cite from legal documents.",
        }

        result = resolver.resolve_prompt(corpus_prompt_config=corpus_config)

        assert result.system_prompt == "You are a legal expert assistant."
        assert result.constraints == "Only cite from legal documents."
        assert result.response_language is None

    def test_prompt_request_body_overrides_corpus(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver

        settings = _make_settings()
        resolver = ConfigResolver(settings)

        corpus_config = {
            "system_prompt": "Corpus-level prompt.",
            "constraints": "Corpus-level constraints.",
        }
        request_body = {"system_prompt": "Request-level prompt override."}

        result = resolver.resolve_prompt(
            request_body=request_body,
            corpus_prompt_config=corpus_config,
        )

        assert result.system_prompt == "Request-level prompt override."
        assert result.constraints == "Corpus-level constraints."

    def test_prompt_corpus_constraints(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver

        settings = _make_settings()
        resolver = ConfigResolver(settings)

        corpus_config = {"constraints": "Custom constraints for this corpus."}
        result = resolver.resolve_prompt(corpus_prompt_config=corpus_config)

        assert result.constraints == "Custom constraints for this corpus."

    def test_prompt_response_language(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver

        settings = _make_settings()
        resolver = ConfigResolver(settings)

        corpus_config = {"response_language": "es"}
        result = resolver.resolve_prompt(corpus_prompt_config=corpus_config)

        assert result.response_language == "es"

    def test_prompt_partial_override(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver
        from callobuzz_rag.retrieval.prompt_templates import CONSTRAINTS_BLOCK

        settings = _make_settings()
        resolver = ConfigResolver(settings)

        corpus_config = {"system_prompt": "Only this is overridden."}
        result = resolver.resolve_prompt(corpus_prompt_config=corpus_config)

        assert result.system_prompt == "Only this is overridden."
        assert result.constraints == CONSTRAINTS_BLOCK
        assert result.response_language is None


class TestEdgeCases:
    def test_resolve_llm_temperature_zero_preserved(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver

        settings = _make_settings()
        resolver = ConfigResolver(settings)

        corpus_config = {"temperature": 0}
        result = resolver.resolve_llm(request_headers=None, corpus_llm_config=corpus_config)
        assert result.temperature == 0.0

    def test_resolve_llm_max_tokens_zero_preserved(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver

        settings = _make_settings()
        resolver = ConfigResolver(settings)

        corpus_config = {"max_tokens": 0}
        result = resolver.resolve_llm(request_headers=None, corpus_llm_config=corpus_config)
        assert result.max_tokens == 0

    def test_resolve_llm_case_insensitive_headers(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver

        settings = _make_settings()
        resolver = ConfigResolver(settings)

        headers = {"X-LLM-Model": "gpt-4o"}
        result = resolver.resolve_llm(request_headers=headers, corpus_llm_config=None)
        assert result.model == "gpt-4o"

    def test_resolve_prompt_empty_string_system_prompt_preserved(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver

        settings = _make_settings()
        resolver = ConfigResolver(settings)

        body = {"system_prompt": ""}
        result = resolver.resolve_prompt(request_body=body, corpus_prompt_config=None)
        assert result.system_prompt == ""


class TestFusionStrategyConfig:
    def test_default_fusion_is_convex_at_three_quarters_dense(self) -> None:
        """Measured default: benchmarks/fusion_study.py (BEIR, tuned on dev)."""
        from callobuzz_rag.core.config import Settings

        s = Settings()
        assert s.fusion_strategy == "convex"
        assert s.hybrid_dense_weight == 0.75

    def test_resolved_retrieval_has_fusion_strategy(self) -> None:
        from callobuzz_rag.core.config import Settings
        from callobuzz_rag.core.config_resolver import ConfigResolver

        resolver = ConfigResolver(Settings())
        resolved = resolver.resolve_retrieval()
        assert resolved.fusion_strategy == "convex"
        assert resolved.hybrid_dense_weight == 0.75
        assert resolved.rrf_k == 60

    def test_resolved_retrieval_corpus_override(self) -> None:
        from callobuzz_rag.core.config import Settings
        from callobuzz_rag.core.config_resolver import ConfigResolver

        resolver = ConfigResolver(Settings())
        resolved = resolver.resolve_retrieval(
            {"fusion_strategy": "rrf", "rrf_k": 30, "hybrid_dense_weight": 0.6}
        )
        assert resolved.fusion_strategy == "rrf"
        assert resolved.rrf_k == 30
        assert resolved.hybrid_dense_weight == 0.6


def test_reranking_follows_the_engine_setting_not_a_hardcoded_default() -> None:
    """It was `True` in the resolver regardless of settings; BEIR says off by default."""
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.core.config_resolver import ConfigResolver

    assert ConfigResolver(Settings()).resolve_retrieval().reranking_enabled is False
    on = ConfigResolver(Settings(reranking_enabled=True)).resolve_retrieval()
    assert on.reranking_enabled is True
    corpus = ConfigResolver(Settings()).resolve_retrieval({"reranking_enabled": True})
    assert corpus.reranking_enabled is True


def test_llm_timeout_reaches_litellm() -> None:
    """LiteLLM waits 6000s by default; every call carries the engine's timeout."""
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.core.config_resolver import ConfigResolver
    from callobuzz_rag.llm.litellm_provider import LiteLLMProvider
    from callobuzz_rag.llm.models import LLMMessage

    settings = Settings(llm_timeout_seconds=7.5)
    resolved = ConfigResolver(settings).resolve_llm()
    assert resolved.timeout == 7.5
    for provider in (LiteLLMProvider(settings), LiteLLMProvider.from_resolved_config(resolved)):
        kwargs = provider._build_kwargs(
            [LLMMessage(role="user", content="x")], model=None, temperature=None, max_tokens=None
        )
        assert kwargs["timeout"] == 7.5
        assert provider.fast_call_options()["timeout"] == settings.llm_fast_timeout_seconds


class TestBaseUrlHeaderNeedsKey:
    """X-LLM-Base-URL must never carry the engine's or the corpus's stored key elsewhere."""

    def test_base_url_header_without_key_is_ignored(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver

        settings = _make_settings()
        resolver = ConfigResolver(settings, encryptor=_make_encryptor())

        result = resolver.resolve_llm(
            request_headers={"x-llm-base-url": "https://attacker.example"},
        )

        assert result.base_url == settings.llm_api_base
        assert result.api_key == "sk-default-engine-key"

    def test_base_url_header_without_key_keeps_the_corpus_base_url(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver

        resolver = ConfigResolver(_make_settings(), encryptor=_make_encryptor())

        result = resolver.resolve_llm(
            request_headers={"x-llm-base-url": "https://attacker.example"},
            corpus_llm_config={"base_url": "https://corpus.example/v1"},
        )

        assert result.base_url == "https://corpus.example/v1"

    def test_base_url_header_with_key_is_honoured(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver

        resolver = ConfigResolver(_make_settings(), encryptor=_make_encryptor())

        result = resolver.resolve_llm(
            request_headers={
                "x-llm-base-url": "https://proxy.example",
                "x-llm-api-key": "sk-caller",
            },
        )

        assert result.base_url == "https://proxy.example"
        assert result.api_key == "sk-caller"
