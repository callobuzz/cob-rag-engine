"""Query API request/response schemas."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from callobuzz_rag.api.schemas.assistant import (
    AssistantReplyFields,
    AssistantRequestOptions,
)


class QueryOptions(BaseModel):
    """Optional query parameters for fine-tuning retrieval.

    `extra='forbid'` is load-bearing, not tidiness. Pydantic's default is
    `extra='ignore'`, and under it a client sent `search_mode: 'hybrid'` for months
    while the engine silently dropped it: the "hybrid fallback" re-ran the
    identical dense query, scored the identical chunks, and reported success.
    Every layer said it worked. An unknown field is now a 422, so a caller that
    is out of step with this engine fails loudly instead of degrading quietly.
    """

    model_config = ConfigDict(extra="forbid")

    top_k: int | None = Field(
        None,
        ge=1,
        le=100,
        description=(
            "RETRIEVAL WIDTH — how many candidates each query vector pulls from the "
            "index, before fusion, reranking, MMR and decay. This is NOT the number "
            "of chunks you get back; that is `final_k`. Setting this to the number "
            "of chunks you want collapses the funnel, because every ranking stage "
            "downstream can only reorder what search already handed it. Leave it "
            "alone unless you are deliberately trading recall for latency."
        ),
    )
    final_k: int | None = Field(
        None,
        ge=1,
        le=100,
        description=(
            "How many chunks are returned, after every ranking stage has run. This "
            "is the knob a caller sizing a context window actually wants. Omit to "
            "use the corpus setting, then the engine default. Bounded above by what "
            "survives the pipeline: `top_k` candidates, and when reranking is on, "
            "`RAG_RERANK_TOP_K_OUTPUT` (10 by default) — ask for 12 and you get 10."
        ),
    )
    enable_hyde: bool | None = Field(
        None,
        description="Enable HyDE for this query (overrides default).",
    )
    enable_classification: bool | None = Field(
        None,
        description="Enable query classification (overrides default).",
    )
    enable_validation: bool | None = Field(
        None,
        description="Enable response validation (overrides default).",
    )
    category: str | None = Field(
        None,
        max_length=50,
        description="Filter by content category (e.g., 'product', 'operations', 'memory').",
    )
    user_id: str | None = Field(
        None,
        max_length=255,
        description="Filter by user ID (for memory scoping).",
    )
    metadata_filters: dict[str, str] | None = Field(
        None,
        description="Arbitrary key-value filters applied to document metadata.",
    )
    search_mode: Literal["dense", "hybrid", "keyword"] | None = Field(
        None,
        description=(
            "Retrieval mode. 'dense' = vector similarity only. 'hybrid' = vector "
            "fused with BM25 keyword search, which is what finds exact identifiers "
            "(SKU, order number, box code) that carry little semantic signal. "
            "'keyword' = BM25 alone, with no embedding call: exact lookups. "
            "Omit to use the corpus setting, then the engine default."
        ),
    )
    category_weights: dict[str, float] | None = Field(
        None,
        description=(
            "How much this caller trusts each content category, as a multiplier on "
            "the final score. Applied after every ranking stage and BEFORE the trim "
            "to `final_k`, so a weight can promote a chunk into the answer or drop "
            "one out of it. A category not listed here — and a chunk with no "
            "category at all — is weighted 1.0: an omission means no opinion, never "
            "a demotion.\n\n"
            "Choose the magnitude deliberately. Scores are RRF, and RRF is a narrow "
            "band: rank 1 scores 1/61 and rank 20 scores 1/80, a spread of only "
            "1.3x. So 0.5 does not 'slightly prefer' — it outweighs the entire "
            "ranking. Use values near 1.0 (0.9/1.1) for a nudge and reserve "
            "aggressive values for a category you genuinely want overridden."
        ),
    )

    @field_validator("category_weights")
    @classmethod
    def _validate_category_weights(cls, value: dict[str, float] | None) -> dict[str, float] | None:
        """Reject weights that are not usable multipliers.

        Zero and negatives are refused rather than clamped: a caller sending 0
        means "never return this category", which is a FILTER (`options.category`),
        not a weight, and silently treating it as one would hide the mistake.
        """
        if value is None:
            return value
        if len(value) > 32:
            raise ValueError("At most 32 category weights may be supplied.")
        for category, weight in value.items():
            if not category or len(category) > 50:
                raise ValueError(f"Category name must be 1-50 characters, got {category!r}.")
            if not 0 < weight <= 10:
                raise ValueError(
                    f"Weight for {category!r} must be in (0, 10], got {weight}. "
                    "To exclude a category entirely use the `category` filter."
                )
        return value


class QueryRequest(BaseModel):
    """Request body for query endpoint.

    See `QueryOptions` for why unknown fields are rejected rather than ignored.
    """

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="The user's question or search query.",
    )
    corpus_id: UUID | None = Field(
        None,
        description=(
            "ID of the corpus to search within. "
            "Optional when using a corpus API key (derived from auth). "
            "Required when using an admin API key."
        ),
    )
    system_prompt: str | None = Field(
        None,
        max_length=4000,
        description=(
            "Per-request system prompt override. "
            "Takes priority over corpus-level and engine-default prompts."
        ),
    )
    options: QueryOptions = Field(
        default_factory=QueryOptions,
        description="Optional retrieval parameters.",
    )
    assistant: AssistantRequestOptions | None = Field(
        None,
        description=(
            "Assistant options: continue a conversation, preview the draft, owner-only "
            "debug trace. Has no effect when the knowledge base's assistant is switched "
            "off. `use_draft`, `debug` and `simulate` need an API key with write permission."
        ),
    )


class RetrieveRequest(QueryRequest):
    """Request body for POST /query/retrieve.

    Retrieval builds no LLM prompt, so the query may be a whole passage — "find what
    is like this" — not just a question. The 2000-character cap on /query protects
    the prompt budget; here it would only cut such searches short.
    """

    query: str = Field(
        ...,
        min_length=1,
        max_length=8000,
        description="The search query: a question, keywords, or a whole passage.",
    )
    # Retrieval builds no reply, so there is no assistant to configure: refused, not ignored.
    assistant: None = Field(None, description="Not accepted by /query/retrieve.")


class AlsoInDocument(BaseModel):
    """Another document that holds the same passage as a returned chunk."""

    document_id: str
    file_name: str | None = None


class CitationSource(BaseModel):
    """A citation pointing to a specific chunk that supports the answer."""

    chunk_id: str
    document_id: str | None = None
    external_id: str | None = None
    section: str | None = None
    file_name: str | None = None
    text_snippet: str = Field(
        ...,
        description="Relevant excerpt from the source chunk.",
    )
    page: int | None = None
    score: float = Field(
        ...,
        description="Relevance score for this citation.",
    )
    also_in: list[AlsoInDocument] = Field(
        default_factory=list,
        description=(
            "Other documents that hold this same passage (at most 5). The passage is "
            "returned once, from the best-scoring copy."
        ),
    )


class QueryResponse(AssistantReplyFields):
    """Response body for query endpoint."""

    answer: str = Field(
        ...,
        description="The generated answer to the query.",
    )
    citations: list[CitationSource] = Field(
        default_factory=list,
        description="Source citations supporting the answer.",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score from validation (0.0 to 1.0).",
    )
    query_id: UUID = Field(
        ...,
        description="ID for this query (use for feedback submission).",
    )
    model_used: str = Field(
        ...,
        description="LLM model used for generation.",
    )
    latency_ms: int = Field(
        ...,
        description="Total query pipeline latency in milliseconds.",
    )
    token_usage: dict[str, Any] | None = Field(
        None,
        description="Token usage breakdown: prompt_tokens, completion_tokens, total_tokens.",
    )


class ScoreBreakdown(BaseModel):
    """Per-stage detail behind a chunk's single `score`.

    `score` is the RRF fusion score, and every stage after fusion only reorders —
    it never rewrites that number. So the score alone cannot say whether a chunk
    arrived by dense similarity, by an exact keyword hit, or because the
    cross-encoder rescued it. A `null` field means the stage did not run.
    """

    fused_score: float = Field(
        ...,
        description=(
            "Score after fusing the search legs — convex (weighted, normalized) by "
            "default, RRF if the corpus chose it. Equals `score` unless "
            "`category_weights` was sent, in which case `score` is this times "
            "`category_weight`."
        ),
    )
    category: str | None = Field(
        None,
        description="The chunk's content category, as indexed. Null if it has none.",
    )
    category_weight: float | None = Field(
        None,
        description=(
            "The multiplier applied from `category_weights`. Null when the caller "
            "sent none; 1.0 when it sent some but had no opinion on this category."
        ),
    )
    pipeline_rank: int = Field(
        ...,
        description=(
            "1-indexed position after every ranking stage (rerank, MMR, decay). "
            "Note this need NOT match the chunk's position in `chunks`: context "
            "assembly re-sorts by `score`, which is the pre-rerank fusion score."
        ),
    )
    dense_rank: int | None = Field(
        None,
        description=(
            "Best 1-indexed rank this chunk reached in any dense (vector) leg. "
            "Null means dense search never returned it — it came from BM25 alone."
        ),
    )
    sparse_rank: int | None = Field(
        None,
        description=(
            "1-indexed rank in the BM25 leg. Null when hybrid search is off for "
            "this query, or when BM25 did not return this chunk."
        ),
    )
    dense_score: float | None = Field(
        None,
        description=(
            "Cosine similarity to the query in the dense leg (best across query "
            "variants). Null when dense search did not return this chunk."
        ),
    )
    sparse_score: float | None = Field(
        None,
        description="BM25 score in the keyword leg. Null when that leg did not return it.",
    )
    rerank_score: float | None = Field(
        None,
        description="Cross-encoder score. Null when reranking is off or it failed.",
    )
    decay_multiplier: float | None = Field(
        None,
        description="Temporal decay factor in (0, 1]. Null when decay is off.",
    )
    age_days: float | None = Field(
        None,
        description="Chunk age in days, as measured for decay. Null when decay is off.",
    )


class RetrievedChunk(BaseModel):
    """A single retrieved chunk without LLM generation."""

    chunk_id: str
    text: str = Field(
        ...,
        description="Full text content of the retrieved chunk.",
    )
    file_name: str | None = None
    document_id: str | None = None
    external_id: str | None = Field(
        None,
        description="The record id given to POST /ingest/records; null for uploaded files.",
    )
    section: str | None = Field(
        None,
        description="The chunk's heading path in its document, e.g. 'Returns > Damaged items'.",
    )
    page: int | None = None
    score: float = Field(
        ...,
        description="Relevance score from retrieval pipeline.",
    )
    token_count: int = Field(
        ...,
        description="Token count of the chunk text.",
    )
    also_in: list[AlsoInDocument] = Field(
        default_factory=list,
        description=(
            "Other documents that hold this same passage (at most 5). The passage is "
            "returned once, from the best-scoring copy."
        ),
    )
    score_breakdown: ScoreBreakdown | None = Field(
        None,
        description=(
            "Per-stage scores behind `score`. Present for every chunk the "
            "pipeline ranked; null only if a chunk somehow reached assembly "
            "without passing through ranking."
        ),
    )


class RetrieveResponse(BaseModel):
    """Response body for retrieval-only endpoint (no LLM generation)."""

    chunks: list[RetrievedChunk] = Field(
        ...,
        description="Retrieved and ranked chunks matching the query.",
    )
    processed_query: str = Field(
        ...,
        description="The query after preprocessing.",
    )
    latency_ms: int = Field(
        ...,
        description="Retrieval pipeline latency in milliseconds.",
    )
    total_chunks: int = Field(
        ...,
        description="Number of chunks returned.",
    )
    stage_timings_ms: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Wall clock per pipeline stage, in the order the stages ran — "
            "`query_understanding`, `embed`, `search_dense`, `search_sparse`, `fuse`, "
            "`load_chunk_text`, `rerank`, `mmr`, `temporal_decay`, `category_weights`, "
            "`assemble`. A stage that did not run is absent rather than zero, so 'the "
            "reranker was instant' and 'the reranker was off' stay distinguishable. "
            "These sum to slightly less than `latency_ms`, which also covers the "
            "un-staged work between them. Added because a ~16s retrieval had to be "
            "attributed by ablation because nothing reported this."
        ),
    )


class QueryHistoryResponse(BaseModel):
    """Response body for query history retrieval."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    query_text: str | None
    response: str | None
    confidence_score: float | None
    latency_ms: int | None
    model_used: str | None
    token_usage: dict[str, Any] | None
    created_at: datetime
