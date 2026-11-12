"""Per-request LLM routing: provider + model + key travel together.

The engine holds no LLM credentials of its own. A caller sends:

    X-LLM-Provider: openrouter
    X-LLM-Model:    anthropic/claude-3.5-haiku
    X-LLM-API-Key:  <their own key>
    X-LLM-Base-URL: https://...        (optional)

The provider is passed to LiteLLM as `custom_llm_provider`, which beats the
`provider/model` prefix convention. That is the whole point: a caller can send a
model name in whatever form their own stack uses and still land on the backend
their key belongs to. Without it, `claude-3-5-haiku-20241022` + an OpenRouter key
routes to Anthropic and 401s — the exact provider/key mismatch this prevents.

Provider validation is checked against `litellm.provider_list` (the real one, not
a fixture) so these tests fail if the installed LiteLLM ever renames a provider
we depend on.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.core.config_resolver import ConfigResolver, ResolvedLLMConfig
from callobuzz_rag.llm.litellm_provider import LiteLLMProvider
from callobuzz_rag.llm.models import LLMMessage
from callobuzz_rag.retrieval.orchestrator import QueryOrchestrator
from callobuzz_rag.vectordb.base import SearchResult


def _make_settings(provider: str = "", fast_model: str = "") -> MagicMock:
    settings = MagicMock()
    settings.llm_provider = provider
    settings.llm_model = "anthropic/claude-sonnet-4-20250514"
    settings.llm_api_key.get_secret_value.return_value = ""
    settings.llm_api_base = None
    settings.llm_temperature = 0.7
    settings.llm_max_tokens = 2048
    settings.llm_fast_model = fast_model
    return settings


class TestProviderResolution:
    def test_header_provider_wins(self) -> None:
        resolver = ConfigResolver(_make_settings(provider="openai"))

        resolved = resolver.resolve_llm(
            request_headers={"X-LLM-Provider": "openrouter"},
            corpus_llm_config={"provider": "anthropic"},
        )

        assert resolved.provider == "openrouter"

    def test_corpus_provider_beats_engine_default(self) -> None:
        resolver = ConfigResolver(_make_settings(provider="openai"))

        resolved = resolver.resolve_llm(corpus_llm_config={"provider": "anthropic"})

        assert resolved.provider == "anthropic"

    def test_engine_default_used_when_nothing_else_set(self) -> None:
        resolver = ConfigResolver(_make_settings(provider="openai"))

        assert resolver.resolve_llm().provider == "openai"

    def test_no_provider_anywhere_stays_none(self) -> None:
        """None means "let LiteLLM infer from the model prefix" — the old
        behaviour, so callers that never send a provider are unaffected."""
        resolver = ConfigResolver(_make_settings())

        assert resolver.resolve_llm().provider is None

    def test_provider_is_normalised(self) -> None:
        resolver = ConfigResolver(_make_settings())

        resolved = resolver.resolve_llm(request_headers={"X-LLM-Provider": "  OpenRouter  "})

        assert resolved.provider == "openrouter"

    def test_unknown_provider_is_rejected_not_passed_through(self) -> None:
        """A typo must not reach LiteLLM, where it would fail deep in the call
        with a message that never names the header at fault."""
        resolver = ConfigResolver(_make_settings())

        resolved = resolver.resolve_llm(request_headers={"X-LLM-Provider": "openroutr"})

        assert resolved.provider is None

    def test_validation_uses_the_installed_litellm_registry(self) -> None:
        """Not a hardcoded list — LiteLLM ships 100+ providers and adds more."""
        import litellm

        known = {str(getattr(p, "value", p)) for p in litellm.provider_list}
        assert {"openrouter", "openai", "anthropic", "ollama"} <= known

        resolver = ConfigResolver(_make_settings())
        for provider in ("openrouter", "openai", "anthropic", "ollama"):
            resolved = resolver.resolve_llm(request_headers={"X-LLM-Provider": provider})
            assert resolved.provider == provider


class TestProviderReachesLiteLLM:
    def _kwargs(self, config: ResolvedLLMConfig) -> dict:
        provider = LiteLLMProvider.from_resolved_config(config)
        return provider._build_kwargs(
            [LLMMessage(role="user", content="hi")],
            model=None,
            temperature=None,
            max_tokens=None,
        )

    def _config(self, **overrides: object) -> ResolvedLLMConfig:
        base: dict = {
            "model": "claude-3-5-haiku-20241022",
            "api_key": "sk-or-caller-key",
            "base_url": None,
            "temperature": 0.7,
            "max_tokens": 2048,
        }
        base.update(overrides)
        return ResolvedLLMConfig(**base)  # type: ignore[arg-type]

    def test_custom_llm_provider_is_sent(self) -> None:
        kwargs = self._kwargs(self._config(provider="openrouter"))

        assert kwargs["custom_llm_provider"] == "openrouter"
        # The model is passed through untouched — LiteLLM does not need a prefix
        # once the provider is explicit.
        assert kwargs["model"] == "claude-3-5-haiku-20241022"
        assert kwargs["api_key"] == "sk-or-caller-key"

    def test_omitted_when_no_provider(self) -> None:
        """Absent, not empty-string: LiteLLM must fall back to prefix inference."""
        kwargs = self._kwargs(self._config())

        assert "custom_llm_provider" not in kwargs

    def test_litellm_honours_explicit_provider_over_prefix(self) -> None:
        """The behaviour the whole design rests on, asserted against the real
        library rather than assumed."""
        import litellm

        model, provider = litellm.get_llm_provider(
            model="claude-3-5-haiku-20241022",
            custom_llm_provider="openrouter",
        )[:2]

        assert provider == "openrouter"
        # Newer LiteLLM releases hand back the model re-prefixed with the provider
        # it chose ("openrouter/claude-..."); older ones return it bare. Either
        # way it must be the OpenRouter route, never an inferred Anthropic one.
        assert model.removeprefix("openrouter/") == "claude-3-5-haiku-20241022"

    def test_base_url_is_forwarded_for_self_hosted_targets(self) -> None:
        """Local/self-hosted models are just a URL — nothing special in code.
        Reachability is a network fact: a private address works when the caller
        runs the engine themselves, and not when the engine is hosted elsewhere."""
        kwargs = self._kwargs(self._config(provider="ollama", base_url="http://192.168.1.50:11434"))

        assert kwargs["api_base"] == "http://192.168.1.50:11434"
        assert kwargs["custom_llm_provider"] == "ollama"


class TestCredentialAwareness:
    def test_has_credentials_false_without_key(self) -> None:
        provider = LiteLLMProvider(_make_settings())

        assert provider.has_credentials is False

    def test_has_credentials_true_with_caller_key(self) -> None:
        config = ResolvedLLMConfig(
            model="gpt-4o",
            api_key="sk-caller",
            base_url=None,
            temperature=0.7,
            max_tokens=2048,
        )

        assert LiteLLMProvider.from_resolved_config(config).has_credentials is True

    def test_base_class_defaults_to_having_credentials(self) -> None:
        """Providers predating this contract keep working unchanged."""
        from callobuzz_rag.llm.base import LLMProvider

        assert LLMProvider.has_credentials.fget(object()) is True  # type: ignore[misc]


class TestFastModel:
    def test_falls_back_to_main_model(self) -> None:
        provider = LiteLLMProvider(_make_settings())

        assert provider.fast_model == "anthropic/claude-sonnet-4-20250514"

    def test_cheap_model_used_when_configured(self) -> None:
        provider = LiteLLMProvider(_make_settings(fast_model="openai/gpt-4o-mini"))

        assert provider.fast_model == "openai/gpt-4o-mini"

    def test_resolver_reads_fast_model_from_corpus_then_settings(self) -> None:
        resolver = ConfigResolver(_make_settings(fast_model="openai/gpt-4o-mini"))

        assert resolver.resolve_llm().fast_model == "openai/gpt-4o-mini"
        assert (
            resolver.resolve_llm(corpus_llm_config={"fast_model": "groq/llama-3.1-8b"}).fast_model
            == "groq/llama-3.1-8b"
        )

    def test_fast_model_is_not_header_overridable(self) -> None:
        """A cost knob, not a caller knob — the caller already picks `model`."""
        resolver = ConfigResolver(_make_settings(fast_model="openai/gpt-4o-mini"))

        resolved = resolver.resolve_llm(request_headers={"X-LLM-Fast-Model": "expensive/model"})

        assert resolved.fast_model == "openai/gpt-4o-mini"


def _orchestrator_settings() -> MagicMock:
    """Settings with query classification ON — the shipped default."""
    values = {
        "query_top_k": 20,
        "query_final_k": 5,
        "query_classification_enabled": True,
        "hyde_enabled": False,
        "rrf_k": 60,
        "qdrant_collection": "chunks",
        "validation_enabled": False,
        "mmr_enabled": False,
        "temporal_decay_enabled": False,
        "rerank_top_k_output": 10,
        "mmr_lambda": 0.7,
        "temporal_decay_half_life_days": 30.0,
        "multi_query_count": 3,
        "query_stream_enabled": True,
    }
    settings = MagicMock()
    for key, value in values.items():
        setattr(settings, key, value)
    return settings


def _make_orchestrator(*, has_credentials: bool) -> tuple[QueryOrchestrator, AsyncMock]:
    embedder = AsyncMock()
    embedding = MagicMock()
    embedding.vector = [0.1] * 10
    embedder.embed_texts = AsyncMock(return_value=[embedding])

    vector_store = AsyncMock()
    vector_store.search = AsyncMock(
        return_value=[SearchResult(id=str(uuid.uuid4()), score=0.9, payload={})]
    )

    llm = AsyncMock()
    # AsyncMock auto-creates truthy attributes, so this must be set explicitly
    # or every provider would look credentialed.
    llm.has_credentials = has_credentials
    llm.fast_model = "openai/gpt-4o-mini"
    # Synchronous on the real provider; an AsyncMock attribute would hand
    # back a coroutine, which cannot be spread into complete_json's kwargs.
    llm.fast_call_options = MagicMock(return_value={"model": "openai/gpt-4o-mini"})

    orchestrator = QueryOrchestrator(
        session=AsyncMock(),
        settings=_orchestrator_settings(),
        embedder=embedder,
        vector_store=vector_store,
        llm_provider=llm,
        context_assembler=AsyncMock(),
    )
    return orchestrator, llm


@pytest.fixture(autouse=True)
def _reset_warning_latch() -> None:
    """The warning is once-per-process by design; reset it so each test observes
    its own behaviour rather than a previous test's latch."""
    import callobuzz_rag.retrieval.orchestrator as orchestrator_module

    orchestrator_module._query_understanding_warned = False


class TestQueryUnderstandingSkip:
    """With no credential, query understanding must be skipped — not attempted
    and swallowed.

    Both LLM calls it makes catch broadly and return a neutral default, so before
    this the engine issued two doomed calls on EVERY query, forever, logging a
    pair of stack traces each time. Retrieval itself needs no LLM, so skipping is
    correct; what was wrong was paying the round-trips to discover that.
    """

    @pytest.mark.asyncio
    async def test_no_credentials_makes_no_llm_calls(self) -> None:
        orchestrator, llm = _make_orchestrator(has_credentials=False)

        await orchestrator.run_retrieval_pipeline(text="what is X?", corpus_id=uuid.uuid4())

        llm.complete_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_retrieval_still_returns_results_without_a_key(self) -> None:
        """Degrade, never fail: only multi-query recall is lost."""
        orchestrator, _ = _make_orchestrator(has_credentials=False)

        result = await orchestrator.run_retrieval_pipeline(
            text="what is X?", corpus_id=uuid.uuid4()
        )

        assert result.processed_query == "what is X?"

    @pytest.mark.asyncio
    async def test_credentials_present_runs_query_understanding(self) -> None:
        orchestrator, llm = _make_orchestrator(has_credentials=True)
        llm.complete_json = AsyncMock(
            return_value=MagicMock(content='{"complexity": "simple", "variants": []}')
        )

        # More than three words: a short query skips query understanding by design.
        await orchestrator.run_retrieval_pipeline(
            text="what is the refund policy for X?", corpus_id=uuid.uuid4()
        )

        assert llm.complete_json.await_count > 0

    @pytest.mark.asyncio
    async def test_warning_is_emitted_once_not_per_query(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A per-request warning on a busy engine buries every other log line."""
        orchestrator, _ = _make_orchestrator(has_credentials=False)

        with caplog.at_level("WARNING", logger="callobuzz_rag.retrieval.orchestrator"):
            for _ in range(3):
                await orchestrator.run_retrieval_pipeline(text="what is X?", corpus_id=uuid.uuid4())

        disabled = [r for r in caplog.records if "Query understanding" in r.message]
        assert len(disabled) == 1
        assert "X-LLM-API-Key" in disabled[0].message
