"""Corpus API schemas — v2 with LLM config, prompt config, and key management."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from callobuzz_rag.core.provider_order import clean_provider_order
from callobuzz_rag.core.schemas import StrictRequest
from callobuzz_rag.llm.language_routing import clean_language_models
from callobuzz_rag.llm.thinking import ThinkingLevel


class LLMConfigInput(StrictRequest):
    """LLM configuration for a corpus. API key is plaintext here — encrypted before storage."""

    provider: str | None = Field(
        None,
        max_length=64,
        description=(
            "LiteLLM provider id for this corpus (e.g. 'openai', 'anthropic', 'openrouter', "
            "'ollama'). Overrides RAG_LLM_PROVIDER; send it together with `model`."
        ),
    )
    model: str | None = Field(
        None,
        description="LiteLLM model string (e.g., 'anthropic/claude-sonnet-4-20250514').",
    )
    api_key: str | None = Field(
        None,
        description=(
            "LLM API key (plaintext). Will be encrypted at rest. On update, null removes "
            "the stored key so the engine default applies again."
        ),
    )
    base_url: str | None = Field(
        None,
        description="Custom API base URL (e.g., for Ollama: 'http://localhost:11434').",
    )
    temperature: float | None = Field(
        None,
        ge=0.0,
        le=2.0,
        description="LLM sampling temperature (0.0-2.0).",
    )
    max_tokens: int | None = Field(
        None,
        ge=1,
        le=100000,
        description="Maximum tokens in LLM response.",
    )
    fast_model: str | None = Field(
        None,
        description="Cheaper model for query understanding only (overrides RAG_LLM_FAST_MODEL).",
    )
    fast_provider_order: list[str] | None = Field(
        None,
        max_length=8,
        description=(
            "OpenRouter only: provider slugs to try first for the fast model, e.g. "
            "['groq', 'cerebras'] (overrides RAG_LLM_FAST_PROVIDER_ORDER). Fallbacks stay "
            "allowed. [] means no pin; null removes it so the engine default applies."
        ),
    )

    @field_validator("fast_provider_order")
    @classmethod
    def _provider_slugs(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if any(not v.strip() for v in value):
            raise ValueError("empty provider slug")
        return clean_provider_order(value)

    thinking: ThinkingLevel | None = Field(
        None,
        description=(
            "How much the answer model thinks before answering this knowledge base's "
            "questions: 'off', 'minimal', 'low', 'medium', 'high' or 'provider-default' "
            "(overrides RAG_LLM_THINKING). Off is fastest and cheapest. A model that cannot "
            "switch thinking off gets its minimum; answers report what it ran at "
            "(thinking_effective). null removes it so the engine default applies."
        ),
    )
    fast_thinking: ThinkingLevel | None = Field(
        None,
        description=(
            "The same for the fast model's helper calls (labels, search rewrites, follow-up "
            "chips); overrides RAG_LLM_FAST_THINKING (default 'off'). null removes it."
        ),
    )
    language_models: dict[str, str] | None = Field(
        None,
        description=(
            "Answer model per reply language: an object from a language code (en, hi, hi-Latn, "
            "mr, ta, te, bn, gu, kn, ml, pa) to a model id of this knowledge base's provider, "
            'e.g. {"hi": "google/gemma-4-31b-it", "hi-Latn": "google/gemma-4-31b-it"}. '
            "A language not listed uses `model`. {} turns language routing off for this "
            "knowledge base; null removes the setting so the engine default "
            "(RAG_LLM_LANGUAGE_MODELS) applies. Ignored when a request sends X-LLM-Model."
        ),
    )

    @field_validator("language_models")
    @classmethod
    def _language_model_map(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return None if value is None else clean_language_models(value)

    reasoning_effort: Literal["none", "low", "medium", "high"] | None = Field(
        None,
        description=(
            "Older name of `thinking` ('none' = 'off'), still honoured when `thinking` is not "
            "set. null removes it."
        ),
    )


class PromptConfigInput(StrictRequest):
    """Prompt configuration for a corpus — customizes RAG behavior per-corpus."""

    system_prompt: str | None = Field(
        None,
        max_length=10000,
        description="Custom system prompt for this corpus.",
    )
    constraints: str | None = Field(
        None,
        max_length=5000,
        description="Additional constraints appended to the system prompt.",
    )
    response_language: str | None = Field(
        None,
        max_length=50,
        description="Preferred response language (e.g., 'en', 'es', 'he').",
    )


class RetrievalConfigInput(StrictRequest):
    """Retrieval configuration for a corpus — presets and/or individual overrides."""

    preset: str | None = Field(
        None,
        description=(
            "Named preset: 'economy' (fast/cheap), 'standard' (balanced), 'premium' (max quality). "
            "When set, all retrieval fields are populated from the preset. "
            "Individual field overrides below take priority over preset values."
        ),
    )
    top_k: int | None = Field(
        None,
        ge=1,
        le=100,
        description=(
            "Retrieval width — candidates pulled per query vector, before fusion, "
            "reranking, MMR and decay. NOT the number of chunks returned; that is "
            "`final_k`. This field was documented as 'final chunks returned' for a "
            "long time, which is how at least one corpus ended up pinned to 5 and "
            "silently lost its whole rerank funnel."
        ),
    )
    final_k: int | None = Field(
        None, ge=1, le=100, description="Chunks returned after all ranking stages."
    )
    multi_query_count: int | None = Field(None, ge=1, le=5, description="Query variant count.")
    hyde_enabled: bool | None = Field(None, description="Enable HyDE expansion.")
    query_classification_enabled: bool | None = Field(
        None, description="Enable complexity classification."
    )
    reranking_enabled: bool | None = Field(None, description="Enable cross-encoder reranking.")
    mmr_enabled: bool | None = Field(None, description="Enable MMR diversity filtering.")
    mmr_lambda: float | None = Field(None, ge=0.0, le=1.0, description="MMR diversity weight.")
    temporal_decay_enabled: bool | None = Field(None, description="Enable temporal decay scoring.")
    validation_enabled: bool | None = Field(None, description="Enable response validation.")
    cache_enabled: bool | None = Field(None, description="Enable query response cache.")
    hybrid_search_enabled: bool | None = Field(
        None,
        description=(
            "Fuse a BM25 sparse leg into the query pipeline alongside dense search. "
            "Engine default on. Dense embeddings are weakest on "
            "exact identifiers — SKUs, order numbers, box codes — which is what "
            "BM25 is best at."
        ),
    )
    fusion_strategy: Literal["convex", "rrf"] | None = Field(
        None,
        description=(
            "How hybrid search combines its dense and keyword legs: 'convex' "
            "(weighted normalized scores, the default — see `hybrid_dense_weight`) or "
            "'rrf' (reciprocal rank fusion, see `rrf_k`)."
        ),
    )
    hybrid_dense_weight: float | None = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Dense share of a convex fusion; the keyword leg gets the rest.",
    )
    rrf_k: int | None = Field(None, ge=1, le=1000, description="RRF smoothing constant.")
    rerank_provider: Literal["local", "llm"] | None = Field(
        None,
        description=(
            "Which reranker to use. 'local' is the self-hosted cross-encoder: "
            "free, runs on this box, and its speed depends entirely on the "
            "device — measured 406ms on GPU and 9.7s on CPU for a real "
            "20-candidate query. 'llm' ranks with the credentials the caller "
            "sends on the X-LLM-* headers, and is billed per query to whoever "
            "owns that key; it is 4-8x faster than the CPU path but slower than "
            "the GPU one, which no network call can beat. A failure on 'llm' "
            "falls back to "
            "'local' rather than turning reranking off. A value outside this set "
            "is a 422 rather than a silent fallback to the default."
        ),
    )
    boilerplate_strip: bool | None = Field(
        None,
        description=(
            "Strip page furniture (menus, footers, cookie notices, text repeated on most "
            "pages of the knowledge base) from documents before they are indexed. Engine "
            "default on; false keeps every page whole. Applies to documents ingested after "
            "the change. Owner only."
        ),
    )
    collapse_duplicates: bool | None = Field(
        None,
        description=(
            "Return a passage that is stored in several documents once, with the other "
            "documents listed as `also_in`. Engine default on."
        ),
    )
    rerank_llm_model: str | None = Field(
        None,
        description=(
            "Model used when `rerank_provider` is 'llm'. Unset means the "
            "caller's cheap model, which is the right default: ranking is a "
            "judgement call, not the answering model's job."
        ),
    )
    pipeline_order_enabled: bool | None = Field(
        None,
        description=(
            "Deliver chunks in the order the pipeline decided, carrying each "
            "stage's own score on `score`. Off, rerank/MMR/decay reorder "
            "the list and context assembly re-sorts it back by the RRF fusion "
            "score, discarding the cross-encoder's ordering. Engine default off: "
            "it changes both the ordering and the `score` number every caller "
            "sees. `score_breakdown.fused_score` carries the raw fusion score "
            "either way, so the two orderings stay comparable."
        ),
    )

    @model_validator(mode="after")
    def validate_preset_name(self) -> "RetrievalConfigInput":
        if self.preset is not None:
            valid = {"economy", "standard", "premium"}
            if self.preset not in valid:
                raise ValueError(f"preset must be one of {valid}, got '{self.preset}'")
        return self


class EmbeddingConfigInput(StrictRequest):
    """Embedding configuration for a corpus. Validated against EMBEDDING_CATALOG."""

    provider: str | None = Field(
        None,
        description="Embedding provider: 'local', 'tei', or 'jina'.",
    )
    model: str | None = Field(
        None,
        description="Model string from EMBEDDING_CATALOG (e.g., 'BAAI/bge-m3').",
    )
    api_key: str | None = Field(
        None,
        description="API key for external providers (plaintext). Encrypted at rest.",
    )


class CreateCorpusRequest(StrictRequest):
    """Request body for creating a new corpus."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    llm_config: LLMConfigInput | None = None
    prompt_config: PromptConfigInput | None = None
    retrieval_config: RetrievalConfigInput | None = None
    embedding_config: EmbeddingConfigInput | None = Field(
        None,
        description=(
            "The corpus's embedding model, from GET /api/v1/models/embeddings (validated "
            "like PUT). Its collection is built with that model; omitted = engine default."
        ),
    )
    tenant_id: UUID | None = Field(
        None,
        description=(
            "Required with the admin key (every knowledge base belongs to a tenant). "
            "A tenant key always creates corpora for itself and may omit it."
        ),
    )


class CreateCorpusResponse(BaseModel):
    """Response for corpus creation — includes the generated API key (shown only once)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None = None
    api_key: str = Field(
        ...,
        description="API key for this corpus. Shown only at creation — store it securely.",
    )
    qdrant_collection: str
    created_at: datetime


class UpdateCorpusRequest(StrictRequest):
    """Request body for updating corpus configuration. All fields optional.

    Only fields present change. Inside llm_config, prompt_config and
    retrieval_config a field sent as null is removed (the engine default applies
    again) and `{}` changes nothing; a whole section sent as null resets it.
    `description: null` clears the description.
    """

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    llm_config: LLMConfigInput | None = None
    prompt_config: PromptConfigInput | None = None
    retrieval_config: RetrievalConfigInput | None = None
    embedding_config: EmbeddingConfigInput | None = None
    tenant_id: UUID | None = Field(
        None,
        description=(
            "Admin only: move the corpus to this tenant (its corpus limit applies). "
            "null is refused (422 tenant_required)."
        ),
    )


class CorpusStats(BaseModel):
    """Ownership and activity figures shown with every corpus."""

    tenant_id: UUID = Field(..., description="The owning tenant (every corpus has one).")
    tenant_name: str
    document_count: int = 0
    chunk_count: int = Field(0, description="Chunks of active document versions (searchable).")
    last_activity_at: datetime | None = Field(
        None, description="The later of the last logged query and the last ingest job."
    )


class CorpusDetailResponse(CorpusStats):
    """Detailed corpus response — includes config but redacts sensitive fields."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    qdrant_collection: str
    llm_config: dict[str, Any] | None = Field(
        None,
        description="LLM config with api_key_encrypted redacted to '***'.",
    )
    prompt_config: dict[str, Any] | None
    retrieval_config: dict[str, Any] | None = None
    embedding_config: dict[str, Any] | None = None
    applied_embedding: dict[str, Any] | None = None
    migration_id: str | None = None
    created_at: datetime


class CorpusResponse(CorpusStats):
    """Lightweight corpus response for list views."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    qdrant_collection: str
    created_at: datetime


class CorpusListResponse(BaseModel):
    """Paginated list of corpora."""

    items: list[CorpusResponse]
    count: int = Field(..., description="Items on this page.")
    total: int = Field(0, description="Corpora matching the filter, across all pages.")
    offset: int = 0
    limit: int = 100


class RotateKeyResponse(BaseModel):
    """Response for key rotation — includes the new API key (shown only once)."""

    api_key: str = Field(
        ...,
        description="New API key for this corpus. The old key is now invalid.",
    )
