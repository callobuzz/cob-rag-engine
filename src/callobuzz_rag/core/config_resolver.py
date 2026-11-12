"""3-tier config resolution: request -> corpus -> engine defaults.

Each field resolves independently through the priority chain:
  1. Request-level (HTTP headers or body) — highest priority
  2. Corpus-level (JSONB config on Corpus model) — mid priority
  3. Engine defaults (Settings / prompt_templates.py) — lowest priority
"""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.provider_order import clean_provider_order
from callobuzz_rag.llm.language_routing import resolve_language_models
from callobuzz_rag.llm.thinking import clean_thinking

if TYPE_CHECKING:
    from callobuzz_rag.core.secrets import FernetEncryptor

logger = logging.getLogger(__name__)


@dataclass
class ResolvedLLMConfig:
    """Fully resolved LLM configuration after 3-tier merge."""

    model: str
    api_key: str
    base_url: str | None
    temperature: float
    max_tokens: int
    fast_model: str | None = None
    fast_provider_order: list[str] = field(default_factory=list)
    """OpenRouter hosts to try first for the fast model (llm_config.fast_provider_order,
    else RAG_LLM_FAST_PROVIDER_ORDER). Not header-overridable."""
    provider: str | None = None
    """LiteLLM provider id ("openrouter", "openai", "anthropic", "ollama", ...).

    When set it is passed to LiteLLM as `custom_llm_provider`, which takes
    priority over the `provider/model` prefix convention. That is what lets a
    caller send an unprefixed model name — `claude-3-5-haiku-20241022` with
    provider `openrouter` routes to OpenRouter, not Anthropic — so the caller
    never has to know LiteLLM's prefix rules to avoid a provider/key mismatch.
    """
    fast_temperature: float = 0.0
    """Sampling temperature for query understanding. Engine setting only."""
    fast_max_tokens: int = 512
    """Token cap for query understanding on a known non-reasoning model."""
    timeout: float = 120.0
    """Seconds per LLM call. Engine setting only (RAG_LLM_TIMEOUT_SECONDS)."""
    fast_timeout: float = 30.0
    """Seconds per query-understanding call (RAG_LLM_FAST_TIMEOUT_SECONDS)."""
    thinking: str | None = None
    """The answer model's thinking: off | minimal | low | medium | high | provider-default;
    None = provider-default. Corpus llm_config.thinking (else its older reasoning_effort),
    else RAG_LLM_THINKING (else RAG_LLM_REASONING_EFFORT). Not header-overridable."""
    fast_thinking: str = "off"
    """The fast model's thinking: corpus llm_config.fast_thinking, else RAG_LLM_FAST_THINKING
    (default off). Not header-overridable."""
    payer: str = "operator"
    """Whose key pays: "operator" (the engine's own LLM key) or "byok" (a key the knowledge
    base stores or the request sends). Usage metering keeps the two apart."""
    language_models: dict[str, str] = field(default_factory=dict)
    """Answer model per reply language that applies to this request (language code -> model
    id, `llm/language_routing.py` for the precedence). Empty = every language uses `model`."""


@dataclass
class ResolvedPromptConfig:
    """Fully resolved prompt configuration after 3-tier merge."""

    system_prompt: str
    constraints: str
    response_language: str | None


@dataclass
class ResolvedRetrievalConfig:
    """Fully resolved retrieval configuration after 2-tier merge.

    Resolution: corpus.retrieval_config -> engine defaults (Settings).
    No request-level override for retrieval settings (safety).
    """

    # `top_k` is the RETRIEVAL WIDTH — how many candidates each query vector pulls
    # out of Qdrant. `final_k` is how many survive to the caller. They are not the
    # same number and setting one to the other's value collapses the funnel:
    # reranking, MMR and temporal decay can only reorder what search handed them.
    top_k: int
    final_k: int
    multi_query_count: int
    hyde_enabled: bool
    query_classification_enabled: bool
    reranking_enabled: bool
    mmr_enabled: bool
    mmr_lambda: float
    temporal_decay_enabled: bool
    validation_enabled: bool
    cache_enabled: bool
    fusion_strategy: str = "convex"
    rrf_k: int = 60
    hybrid_dense_weight: float = 0.75
    hybrid_search_enabled: bool = False
    pipeline_order_enabled: bool = False
    # "local" (self-hosted cross-encoder, free, slow) or "llm" (the caller's own
    # key, faster, paid). Unknown values resolve to "local": this field decides
    # whether money is spent, so an unreadable value must land on the free side.
    rerank_provider: str = "local"
    rerank_llm_model: str | None = None
    # A passage stored in several documents is returned once, with the others as `also_in`.
    collapse_duplicates: bool = True
    # Page furniture is stripped from documents before indexing (an ingest setting, resolved
    # here so the engine info can report what an unset knowledge base does).
    boilerplate_strip: bool = True


@dataclass
class ResolvedEmbeddingConfig:
    """Fully resolved embedding configuration after 2-tier merge.

    Resolution: corpus.embedding_config -> engine defaults (Settings).
    No request-level override — queries must embed with the same model
    as the corpus's stored vectors.
    """

    provider: str
    model: str
    dimensions: int
    api_key: str | None = None


class ConfigResolver:
    """3-tier config resolver: request -> corpus -> engine defaults.

    Each field resolves independently through the chain. The resolver
    is stateless and can be shared across requests.
    """

    def __init__(
        self,
        settings: Settings,
        encryptor: "FernetEncryptor | None" = None,
    ) -> None:
        self._settings = settings
        self._encryptor = encryptor

    def resolve_llm(
        self,
        request_headers: dict[str, str] | None = None,
        corpus_llm_config: dict[str, Any] | None = None,
        payer_override: str | None = None,  # PIP1 profile: set only by tiers.apply.finalize_llm
    ) -> ResolvedLLMConfig:
        """Resolve LLM configuration through the 3-tier chain.

        Field resolution (each field independent):
            model: header X-LLM-Model -> corpus.model -> settings.llm_model
            api_key: header X-LLM-API-Key -> corpus.api_key_encrypted (decrypted)
                     -> settings.llm_api_key
            base_url: header X-LLM-Base-URL -> corpus.base_url
                      -> settings.llm_api_base
            temperature: corpus.temperature -> settings.llm_temperature
            max_tokens: corpus.max_tokens -> settings.llm_max_tokens

        Note: temperature and max_tokens are NOT overridable via request
        headers to prevent abuse.
        """
        headers = self._normalize_headers(request_headers)
        corpus = corpus_llm_config or {}

        # --- model (explicit is-not-None to preserve empty string overrides) ---
        model = self._settings.llm_model
        if corpus.get("model") is not None:
            model = corpus["model"]
        if headers.get("x-llm-model") is not None:
            model = headers["x-llm-model"]

        # --- api_key ---
        api_key = self._resolve_api_key(headers, corpus)

        # --- base_url ---
        base_url = self._settings.llm_api_base
        if corpus.get("base_url") is not None:
            base_url = corpus["base_url"]
        if headers.get("x-llm-base-url") is not None:
            if headers.get("x-llm-api-key"):
                base_url = headers["x-llm-base-url"]
            else:
                # Without the caller's own key the request would carry the engine's (or the
                # knowledge base's) stored key to a host the caller picked.
                logger.warning("Ignoring X-LLM-Base-URL: it needs the caller's own X-LLM-API-Key")

        # --- temperature (not overridable via headers) ---
        temperature = corpus.get("temperature")
        if temperature is None:
            temperature = self._settings.llm_temperature

        # --- max_tokens (not overridable via headers) ---
        max_tokens = corpus.get("max_tokens")
        if max_tokens is None:
            max_tokens = self._settings.llm_max_tokens

        # --- provider (header -> corpus -> engine default) ---
        provider = self._resolve_provider(headers, corpus)

        # --- fast_model (corpus -> engine default) ---
        # Query understanding (classification, decomposition, variants) runs on
        # this instead of the answer model. Not header-overridable: it is a cost
        # knob, and the caller already controls it by choosing `model`.
        fast_model = corpus.get("fast_model") or self._settings.llm_fast_model or None

        # --- fast_provider_order (corpus -> engine default; [] on the corpus = no pin) ---
        stored_order = corpus.get("fast_provider_order")
        fast_provider_order = list(self._settings.llm_fast_provider_order)
        if isinstance(stored_order, list):
            try:
                fast_provider_order = clean_provider_order(str(v) for v in stored_order)
            except ValueError:
                logger.warning("Ignoring invalid corpus fast_provider_order %r", stored_order)

        # --- thinking (corpus -> engine default; not header-overridable) ---
        # Each tier reads the new name first and the older reasoning_effort (none = off)
        # second, so a stored or configured effort keeps meaning what it meant.
        thinking = self._stored_thinking(corpus, "thinking") or self._stored_thinking(
            corpus, "reasoning_effort"
        )
        if thinking is None:
            thinking = clean_thinking(getattr(self._settings, "llm_thinking", None)) or (
                clean_thinking(getattr(self._settings, "llm_reasoning_effort", None))
            )
        fast_thinking = (
            self._stored_thinking(corpus, "fast_thinking")
            or clean_thinking(getattr(self._settings, "llm_fast_thinking", None))
            or "off"
        )

        same_endpoint = (provider or None) == (
            (self._settings.llm_provider or "").strip().lower() or None
        ) and base_url == self._settings.llm_api_base
        engine_map = getattr(self._settings, "llm_language_models", None)
        language_models = resolve_language_models(
            engine=dict(engine_map) if isinstance(engine_map, dict) else {},
            corpus=corpus,
            header_model=headers.get("x-llm-model") is not None,
            same_endpoint=same_endpoint,
        )

        return ResolvedLLMConfig(
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=float(temperature),
            max_tokens=int(max_tokens),
            fast_model=fast_model,
            fast_provider_order=fast_provider_order,
            provider=provider,
            # Engine settings, like temperature above: not header-overridable.
            fast_temperature=float(self._settings.llm_fast_temperature),
            fast_max_tokens=int(self._settings.llm_fast_max_tokens),
            timeout=float(self._settings.llm_timeout_seconds),
            fast_timeout=float(self._settings.llm_fast_timeout_seconds),
            thinking=thinking,
            fast_thinking=fast_thinking,
            payer=payer_override
            or ("operator" if api_key == self._settings.llm_api_key.get_secret_value() else "byok"),
            language_models=language_models,
        )

    @staticmethod
    def _stored_thinking(corpus: dict[str, Any], key: str) -> str | None:
        """A thinking level stored on the knowledge base under `key`; an unreadable value is
        logged and ignored (the next tier applies)."""
        stored = corpus.get(key)
        if stored is None:
            return None
        level = clean_thinking(stored)
        if level is None:
            logger.warning("Ignoring unknown corpus %s %r", key, stored)
        return level

    def _resolve_provider(self, headers: dict[str, str], corpus: dict[str, Any]) -> str | None:
        """Resolve the LiteLLM provider id through the 3-tier chain.

        An unknown provider is rejected here rather than passed through, because
        LiteLLM would otherwise fail deep inside the call with a message that
        does not name the header at fault.
        """
        provider = self._settings.llm_provider or None
        if corpus.get("provider"):
            provider = corpus["provider"]
        if headers.get("x-llm-provider"):
            provider = headers["x-llm-provider"]

        if not provider:
            return None

        provider = provider.strip().lower()
        if not self._is_known_provider(provider):
            logger.warning(
                "Unknown LLM provider %r — ignoring it and falling back to LiteLLM's "
                "model-prefix inference. Send a provider id from litellm.provider_list "
                "(e.g. 'openrouter', 'openai', 'anthropic', 'ollama').",
                provider,
            )
            return None
        return provider

    @staticmethod
    def _is_known_provider(provider: str) -> bool:
        """Check a provider id against LiteLLM's own registry.

        Deliberately not a hardcoded list — LiteLLM ships 100+ providers and adds
        more every release. Asking the installed library keeps this correct
        without anyone remembering to update a constant here.
        """
        try:
            import litellm  # deferred: heavy import
        except ImportError:  # pragma: no cover — litellm is a hard dependency
            return True
        return provider in {str(getattr(p, "value", p)) for p in litellm.provider_list}

    def resolve_prompt(
        self,
        request_body: dict[str, Any] | None = None,
        corpus_prompt_config: dict[str, Any] | None = None,
    ) -> ResolvedPromptConfig:
        """Resolve prompt configuration through the 3-tier chain.

        Field resolution (each field independent):
            system_prompt: body.system_prompt -> corpus.system_prompt
                           -> SYSTEM_PROMPT constant
            constraints: corpus.constraints -> CONSTRAINTS_BLOCK constant
                         (NOT overridable via request body for safety)
            response_language: corpus.response_language -> None
        """
        from callobuzz_rag.retrieval.prompt_templates import CONSTRAINTS_BLOCK, SYSTEM_PROMPT

        body = request_body or {}
        corpus = corpus_prompt_config or {}

        # --- system_prompt (explicit is-not-None to preserve empty string) ---
        system_prompt = SYSTEM_PROMPT
        if corpus.get("system_prompt") is not None:
            system_prompt = corpus["system_prompt"]
        if body.get("system_prompt") is not None:
            system_prompt = body["system_prompt"]

        # --- constraints (not overridable via request for safety) ---
        constraints = CONSTRAINTS_BLOCK
        if corpus.get("constraints") is not None:
            constraints = corpus["constraints"]

        # --- response_language (corpus-only) ---
        response_language = None
        if corpus.get("response_language") is not None:
            response_language = corpus["response_language"]

        return ResolvedPromptConfig(
            system_prompt=system_prompt,
            constraints=constraints,
            response_language=response_language,
        )

    def resolve_retrieval(
        self,
        corpus_retrieval_config: dict[str, Any] | None = None,
    ) -> ResolvedRetrievalConfig:
        """Resolve retrieval configuration: corpus -> engine defaults.

        Each field resolves independently. No request-level override
        for retrieval settings to prevent abuse from end users.
        """
        corpus = corpus_retrieval_config or {}

        def _get(key: str, default: Any) -> Any:
            val = corpus.get(key)
            return val if val is not None else default

        return ResolvedRetrievalConfig(
            top_k=int(_get("top_k", self._settings.query_top_k)),
            final_k=int(_get("final_k", self._settings.query_final_k)),
            multi_query_count=int(_get("multi_query_count", self._settings.multi_query_count)),
            hyde_enabled=bool(_get("hyde_enabled", self._settings.hyde_enabled)),
            query_classification_enabled=bool(
                _get(
                    "query_classification_enabled",
                    self._settings.query_classification_enabled,
                )
            ),
            reranking_enabled=bool(_get("reranking_enabled", self._settings.reranking_enabled)),
            mmr_enabled=bool(_get("mmr_enabled", self._settings.mmr_enabled)),
            mmr_lambda=float(_get("mmr_lambda", self._settings.mmr_lambda)),
            temporal_decay_enabled=bool(
                _get("temporal_decay_enabled", self._settings.temporal_decay_enabled)
            ),
            validation_enabled=bool(_get("validation_enabled", self._settings.validation_enabled)),
            cache_enabled=bool(_get("cache_enabled", True)),
            fusion_strategy=str(_get("fusion_strategy", self._settings.fusion_strategy)),
            rrf_k=int(_get("rrf_k", self._settings.rrf_k)),
            hybrid_dense_weight=float(
                _get("hybrid_dense_weight", self._settings.hybrid_dense_weight)
            ),
            hybrid_search_enabled=bool(
                _get("hybrid_search_enabled", self._settings.hybrid_search_enabled)
            ),
            pipeline_order_enabled=bool(
                _get("pipeline_order_enabled", self._settings.pipeline_order_enabled)
            ),
            # Fail to the free provider. The corpus row is written by an admin UI
            # and by the optimizer, so an unexpected string is possible; spending
            # the consumer's money on one is not an acceptable way to find out.
            rerank_provider=self._normalize_rerank_provider(
                _get("rerank_provider", self._settings.rerank_provider)
            ),
            rerank_llm_model=(
                _get("rerank_llm_model", self._settings.rerank_llm_model or None) or None
            ),
            collapse_duplicates=bool(
                _get("collapse_duplicates", self._settings.collapse_duplicates)
            ),
            boilerplate_strip=bool(
                _get("boilerplate_strip", self._settings.boilerplate_strip_enabled)
            ),
        )

    @staticmethod
    def _normalize_rerank_provider(value: Any) -> str:
        """Accept only a known provider; anything else is `local`."""
        # PIP1 profile: `api` is the hosted reranking service (retrieval/reranking/api.py)
        if isinstance(value, str) and value.lower() in ("local", "llm", "api"):
            return value.lower()
        logger.warning("Unknown rerank_provider %r; using 'local'", value)
        return "local"

    def resolve_embedding(
        self,
        corpus_embedding_config: dict[str, object] | None = None,
    ) -> ResolvedEmbeddingConfig:
        """Resolve embedding configuration: corpus -> engine defaults.

        2-tier only — no request-level override (queries must use the same
        model as the corpus's stored vectors). Validates against the
        embedding catalog; dimensions come from the catalog, never free-typed.
        """
        from callobuzz_rag.core.model_catalog import get_embedding_catalog_entry

        corpus = corpus_embedding_config or {}

        provider = (
            str(corpus["provider"])
            if corpus.get("provider") is not None
            else self._settings.embedding_provider
        )
        model = (
            str(corpus["model"])
            if corpus.get("model") is not None
            else self._settings.embedding_model
        )
        api_key: str | None = None

        encrypted = corpus.get("api_key_encrypted")
        if encrypted:
            if self._encryptor is None:
                logger.warning(
                    "Corpus has encrypted embedding API key but no encryptor configured. "
                    "Falling through to engine default."
                )
            else:
                try:
                    api_key = self._encryptor.decrypt(str(encrypted))
                except Exception:
                    logger.warning(
                        "Failed to decrypt corpus embedding API key.",
                        exc_info=True,
                    )

        entry = get_embedding_catalog_entry(model, provider=provider)
        if (
            entry is None
            and provider == self._settings.embedding_provider
            and model == self._settings.embedding_model
        ):
            # The operator's own configured default is trusted even when it is
            # not in the catalog: any LiteLLM embedding model can be the engine
            # default by setting RAG_EMBEDDING_MODEL + RAG_EMBEDDING_DIMENSIONS.
            # Per-corpus overrides still have to name a catalog model.
            entry = {
                "provider": provider,
                "model": model,
                "dimensions": self._settings.embedding_dimensions,
            }
        if entry is None:
            raise ValueError(f"No catalog entry for provider='{provider}', model='{model}'.")

        return ResolvedEmbeddingConfig(
            provider=entry["provider"],
            model=entry["model"],
            dimensions=entry["dimensions"],
            api_key=api_key,
        )

    def _resolve_api_key(
        self,
        headers: dict[str, str],
        corpus: dict[str, Any],
    ) -> str:
        """Resolve API key through the 3-tier chain."""
        # Tier 1: Request header
        header_key = headers.get("x-llm-api-key")
        if header_key:
            return header_key

        # Tier 2: Corpus encrypted key
        encrypted_key = corpus.get("api_key_encrypted")
        if encrypted_key:
            decrypted = self._decrypt_corpus_key(corpus)
            if decrypted is not None:
                return decrypted

        # Tier 3: Engine default
        return self._settings.llm_api_key.get_secret_value()

    def _decrypt_corpus_key(self, corpus_config: dict[str, Any]) -> str | None:
        """Decrypt api_key_encrypted from corpus config."""
        if self._encryptor is None:
            logger.warning(
                "Corpus has encrypted API key but no encryptor configured. "
                "Falling through to engine default."
            )
            return None

        encrypted_value = corpus_config.get("api_key_encrypted")
        if not encrypted_value:
            return None

        try:
            return self._encryptor.decrypt(encrypted_value)
        except Exception:
            logger.warning(
                "Failed to decrypt corpus API key. Falling through to engine default.",
                exc_info=True,
            )
            return None

    @staticmethod
    def _normalize_headers(
        headers: dict[str, str] | None,
    ) -> dict[str, str]:
        """Normalize header keys to lowercase."""
        if not headers:
            return {}
        return {k.lower(): v for k, v in headers.items()}
