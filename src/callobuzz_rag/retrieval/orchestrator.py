"""Query pipeline orchestrator.

Wires all Phase 2 retrieval providers into a complete query flow.
All providers are injected via constructor (dependency injection).
Follows the same pattern as IngestionOrchestrator from Phase 1.
"""

import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.exceptions import QueryError, RetrievalError
from callobuzz_rag.core.tracing import get_current_trace, trace
from callobuzz_rag.db.models.chunk import Chunk
from callobuzz_rag.db.models.query_log import QueryLog
from callobuzz_rag.embeddings.base import EmbeddingProvider
from callobuzz_rag.llm.base import LLMProvider
from callobuzz_rag.llm.cost import request_cost_of
from callobuzz_rag.retrieval.context_assembly import (
    AssembledContext,
    ContextAssembler,
    ContextSearchResult,
)
from callobuzz_rag.retrieval.dedupe import collapse_duplicate_passages, overfetch
from callobuzz_rag.retrieval.models import (
    ChunkScoreBreakdown,
    QueryComplexity,
    QueryPlan,
)
from callobuzz_rag.retrieval.query_heuristics import skips_query_understanding
from callobuzz_rag.retrieval.reranking.base import RerankProvider
from callobuzz_rag.retrieval.temporal_decay import DecayResult
from callobuzz_rag.vectordb.base import SparseVector, VectorStorageProvider

logger = logging.getLogger(__name__)

_query_understanding_warned = False


def _warn_query_understanding_disabled() -> None:
    """Warn once per process that query understanding is switched off.

    Once per process, not per request: this is a deployment-configuration fact,
    and a per-query warning on a busy engine buries everything else in the log.
    """
    global _query_understanding_warned
    if _query_understanding_warned:
        return
    _query_understanding_warned = True
    logger.warning(
        "Query understanding (classification, decomposition, query variants) is "
        "DISABLED: no LLM credential resolved for this request. Retrieval still "
        "works and results are still ranked — only multi-query recall is lost. "
        "To enable it, send X-LLM-Provider, X-LLM-Model and X-LLM-API-Key with "
        "the request, or set RAG_LLM_PROVIDER/RAG_LLM_MODEL/RAG_LLM_API_KEY to "
        "give the engine a default of its own."
    )


def _write_scores_back(results: list[Any], scores: dict[str, float]) -> list[Any]:
    """Carry a post-fusion stage's own score on `SearchResult.score`.

    Each stage that reorders should also own the number, otherwise `score` keeps
    claiming to be the RRF fusion score long after the ranking stopped being
    based on it — and anything downstream that sorts by `score` silently undoes
    the stage. `score_breakdown.fused_score` keeps the raw fusion score, so the
    old and new orderings stay comparable on any query.

    A result the stage did not score is left untouched rather than zeroed: the
    reranker returns only its top `rerank_top_k_output`, and dropping the rest to
    0.0 would reorder chunks the stage never expressed an opinion about.
    """
    from dataclasses import replace

    if not scores:
        return results
    return [replace(r, score=scores[r.id]) if r.id in scores else r for r in results]


class _StageTimings:
    """Wall clock per pipeline stage, in the order the stages ran.

    Retrieval takes ~16s on a production corpus and there was no way to see where it
    went. The published breakdown — embedding+search a 0.76s floor, the CPU
    cross-encoder ~0.5s per candidate above it, query understanding a further 5-9s — had
    to be produced by **ablation**: re-running one query at six values of `top_k` and
    again with classification and HyDE toggled, and subtracting. That measures the shape
    of the cost, not any individual request, and it cannot be done at all on production
    traffic.

    A stage that does not run is absent rather than zero, so "reranking took 0ms" and
    "reranking was off" stay distinguishable — the same distinction as between empty and
    unavailable.

    Costs one `time.monotonic()` pair per stage, so it is always on. Sampling would defeat
    the point: the queries worth explaining are the slow ones, and they are the minority.
    """

    def __init__(self, on_stage: Callable[[str], None] | None = None) -> None:
        self._elapsed: dict[str, float] = {}
        # Told the name of each stage as it STARTS, so a streaming caller can show progress
        # while the pipeline is still running. Must not block or raise.
        self._on_stage = on_stage

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        if self._on_stage is not None:
            self._on_stage(name)
        start = time.monotonic()
        try:
            yield
        finally:
            # Accumulate rather than overwrite: a stage entered more than once (a retry,
            # or a per-variant loop) should report its total, not its last visit.
            self._elapsed[name] = self._elapsed.get(name, 0.0) + (time.monotonic() - start) * 1000

    def as_dict(self) -> dict[str, int]:
        return {name: round(ms) for name, ms in self._elapsed.items()}

    def summary(self) -> str:
        """`embed=41ms search_dense=712ms rerank=9840ms` — pipeline order preserved."""
        return " ".join(f"{name}={ms}ms" for name, ms in self.as_dict().items())


@dataclass
class _RetrievalPipelineResult:
    """Intermediate result from shared retrieval steps 1-13."""

    processed_query: str
    assembled: AssembledContext
    plan: QueryPlan
    top_results: list[Any] = field(default_factory=list)
    score_breakdown: dict[str, ChunkScoreBreakdown] = field(default_factory=dict)
    stage_timings_ms: dict[str, int] = field(default_factory=dict)
    # For each returned chunk id: the other documents that hold the same passage (at most
    # five) - collapsed into it by `retrieval/dedupe.py`.
    also_in: dict[str, list[dict[str, str | None]]] = field(default_factory=dict)
    # Every query that was embedded: the plan's own plus the caller's `extra_queries`.
    queries: list[str] = field(default_factory=list)


class QueryOrchestrator:
    """Orchestrates the complete query pipeline.

    All providers are injected via constructor. The orchestrator
    calls them in sequence, handling errors at each step.
    """

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        embedder: EmbeddingProvider,
        vector_store: VectorStorageProvider,
        llm_provider: LLMProvider,
        context_assembler: ContextAssembler,
        reranker: RerankProvider | None = None,
        collection_name: str | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._embedder = embedder
        self._vector_store = vector_store
        self._llm = llm_provider
        self._assembler = context_assembler
        self._reranker = reranker
        self._collection_name = collection_name or settings.qdrant_collection

    @property
    def llm(self) -> LLMProvider:
        """Public accessor for LLM provider (used by streaming endpoint)."""
        return self._llm

    async def run_retrieval_pipeline(
        self,
        text: str,
        corpus_id: uuid.UUID,
        top_k: int | None = None,
        final_k: int | None = None,
        enable_hyde: bool | None = None,
        enable_classification: bool | None = None,
        enable_mmr: bool | None = None,
        enable_reranking: bool | None = None,
        enable_temporal_decay: bool | None = None,
        enable_hybrid: bool | None = None,
        keyword_only: bool = False,
        enable_pipeline_order: bool | None = None,
        system_prompt: str | None = None,
        constraints: str | None = None,
        response_language: str | None = None,
        category: str | None = None,
        category_weights: dict[str, float] | None = None,
        user_id: str | None = None,
        metadata_filters: Mapping[str, str | list[str]] | None = None,
        context_budget: bool = True,
        on_stage: Callable[[str], None] | None = None,
        extra_queries: list[str] | None = None,
        updated_tz: str | None = None,  # TV5 freshness
    ) -> _RetrievalPipelineResult:
        """Public wrapper for the retrieval pipeline (steps 1-13).

        `on_stage(name)` is called as each timed stage starts (the `stage_timings_ms` keys).
        """
        return await self._run_retrieval_pipeline(
            text,
            corpus_id,
            top_k=top_k,
            final_k=final_k,
            enable_hyde=enable_hyde,
            enable_classification=enable_classification,
            enable_mmr=enable_mmr,
            enable_reranking=enable_reranking,
            enable_temporal_decay=enable_temporal_decay,
            enable_hybrid=enable_hybrid,
            keyword_only=keyword_only,
            enable_pipeline_order=enable_pipeline_order,
            system_prompt=system_prompt,
            constraints=constraints,
            response_language=response_language,
            category=category,
            category_weights=category_weights,
            user_id=user_id,
            metadata_filters=metadata_filters,
            context_budget=context_budget,
            on_stage=on_stage,
            extra_queries=extra_queries,
            updated_tz=updated_tz,  # TV5 freshness
        )

    @trace("query_pipeline.retrieval")
    async def _run_retrieval_pipeline(
        self,
        text: str,
        corpus_id: uuid.UUID,
        top_k: int | None = None,
        final_k: int | None = None,
        enable_hyde: bool | None = None,
        enable_classification: bool | None = None,
        enable_mmr: bool | None = None,
        enable_reranking: bool | None = None,
        enable_temporal_decay: bool | None = None,
        enable_hybrid: bool | None = None,
        keyword_only: bool = False,
        enable_pipeline_order: bool | None = None,
        system_prompt: str | None = None,
        constraints: str | None = None,
        response_language: str | None = None,
        category: str | None = None,
        category_weights: dict[str, float] | None = None,
        user_id: str | None = None,
        metadata_filters: Mapping[str, str | list[str]] | None = None,
        context_budget: bool = True,
        on_stage: Callable[[str], None] | None = None,
        extra_queries: list[str] | None = None,
        updated_tz: str | None = None,  # TV5 freshness
    ) -> _RetrievalPipelineResult:
        """Shared retrieval steps 1-13 used by both query() and query_stream()."""
        timings = _StageTimings(on_stage)
        resolved_top_k = self._settings.query_top_k if top_k is None else top_k
        resolved_final_k = self._settings.query_final_k if final_k is None else final_k

        # --- Step 1: Preprocess query ---
        processed_query = text.strip()
        if not processed_query:
            raise QueryError("Empty query after preprocessing")

        # --- Step 2: Classify query complexity ---
        use_classification = (
            enable_classification
            if enable_classification is not None
            else self._settings.query_classification_enabled
        )
        plan = QueryPlan(
            complexity=QueryComplexity.SIMPLE,
            original_query=processed_query,
        )

        # No credential -> skip rather than issue calls that are certain to fail.
        # The engine holds no LLM key of its own; a caller that wants query
        # understanding sends X-LLM-Provider / X-LLM-Model / X-LLM-API-Key. Without
        # them, classification and variant generation would each raise, be caught,
        # and return their neutral default — two wasted round-trips on EVERY query,
        # forever, with a pair of stack traces per request. Retrieval itself needs
        # no LLM, so degrading quietly here is correct; the warning says why.
        if use_classification and not self._llm.has_credentials:
            _warn_query_understanding_disabled()
            use_classification = False

        # A greeting or a three-word query has nothing to rephrase: skip the classification,
        # decomposition and rephrasing calls (seconds on a thinking model) and search as is.
        # Only when the caller left the choice to the engine: an explicit request for query
        # understanding or HyDE is honoured, however short the query.
        caller_chose = enable_classification is not None or bool(enable_hyde)
        if use_classification and not caller_chose and skips_query_understanding(processed_query):
            logger.debug("Skipping query understanding for short or small-talk query")
            use_classification = False

        if use_classification:
            with timings.stage("query_understanding"):
                plan = await self._build_query_plan(
                    processed_query,
                    enable_hyde=(
                        enable_hyde if enable_hyde is not None else self._settings.hyde_enabled
                    ),
                )

        # --- Step 5: Embed all queries ---
        all_queries = plan.all_queries
        # Extra rephrasings from the caller (C2: a translated query for Hindi/Hinglish
        # questions over English documents). Deduplicated like the plan's own variants.
        extras = [q.strip() for q in (extra_queries or []) if q.strip()]
        seen = {q.lower() for q in all_queries}
        for extra in extras:
            if extra.lower() not in seen:
                seen.add(extra.lower())
                all_queries.append(extra)
        embeddings: list[Any] = []

        # Keyword-only search (exact lookups: SKUs, order numbers) needs no vectors,
        # so it skips the embedding call and the dense legs entirely.
        if not keyword_only:
            try:
                with timings.stage("embed"):
                    embeddings = await self._embedder.embed_texts(
                        all_queries, task="retrieval.query"
                    )
                    if plan.hyde_text:
                        # HyDE's text is a hypothetical ANSWER, compared with passages as
                        # a passage: it gets the passage instruction, not the query one.
                        embeddings += await self._embedder.embed_texts(
                            [plan.hyde_text], task="retrieval.passage"
                        )
            except Exception as e:
                raise RetrievalError(f"Embedding failed: {e}") from e

        # A passage stored in several documents is shown once (RAG_COLLAPSE_DUPLICATES, or
        # the knowledge base's own setting): ask each leg for 50% more so top-k stays whole.
        # `is True`: a settings object without the field (a test double) leaves it off.
        collapse = getattr(self._settings, "collapse_duplicates", False) is True
        search_k = overfetch(resolved_top_k, collapse)

        # --- Steps 6-7: Search per query vector ---
        qdrant_filters: dict[str, Any] = {"corpus_id": str(corpus_id)}
        if category is not None:
            qdrant_filters["category"] = category
        if user_id is not None:
            qdrant_filters["user_id"] = user_id
        if metadata_filters:
            for key, value in metadata_filters.items():
                qdrant_filters[f"meta_{key}"] = value

        all_search_results: list[list[Any]] = []
        # Best rank a chunk reached in ANY dense leg. With variants on there is
        # one leg per rephrasing, and "rank 1 for the rephrasing, rank 30 for the
        # original" is exactly the case worth being able to see afterwards.
        dense_ranks: dict[str, int] = {}
        # Its best cosine similarity across those legs, reported alongside the rank.
        dense_scores: dict[str, float] = {}
        # Timed as one stage rather than per variant: the number of legs is what the
        # variant count buys, and separating them would report N stages that differ only
        # by which rephrasing they searched.
        with timings.stage("search_dense"):
            for emb in embeddings:
                try:
                    results = await self._vector_store.search(
                        self._collection_name,
                        emb.vector,
                        search_k,
                        qdrant_filters,
                    )
                    for rank, result in enumerate(results, 1):
                        if rank < dense_ranks.get(result.id, rank + 1):
                            dense_ranks[result.id] = rank
                        if result.score > dense_scores.get(result.id, float("-inf")):
                            dense_scores[result.id] = result.score
                    all_search_results.append(results)
                except Exception as e:
                    logger.warning("Search failed for one query variant: %s", e)
                    continue

        # --- Step 7b: BM25 sparse search (optional), fused with the dense legs ---
        #
        # Dense embeddings are weakest exactly where operations lives: SKUs, order
        # numbers, box codes. Those are near-signal-free semantically but trivial
        # for BM25. The sparse vectors are written for every chunk at ingest, so
        # this searches an index that already exists.
        #
        # Its results join `all_search_results` as one more ranked list. That is
        # precisely what RRF is for — it fuses on RANK, so a dense cosine score
        # and a BM25 score never have to be made comparable. No normalisation, no
        # weighting to tune.
        #
        # A sparse failure must not take the query down: the dense legs are a
        # complete answer on their own, so it is logged and dropped.
        use_hybrid = keyword_only or (
            enable_hybrid if enable_hybrid is not None else self._settings.hybrid_search_enabled
        )
        sparse_hits = 0
        sparse_ranks: dict[str, int] = {}
        sparse_scores: dict[str, float] = {}
        if use_hybrid:
            from callobuzz_rag.retrieval.sparse import query_to_sparse_vector

            with timings.stage("search_sparse"):
                # Same analyzer as ingest: stopwords out, stems in, identifiers such as
                # "ROLLER-10" kept as `roller` + `10`.
                sparse_data = query_to_sparse_vector(" ".join([processed_query, *extras]))

                if sparse_data.is_empty():
                    logger.debug("Sparse vector empty for %r — skipping BM25 leg", processed_query)
                    if keyword_only:
                        # Nothing to search for (stopwords only): an empty answer,
                        # not a failed search.
                        all_search_results.append([])
                else:
                    try:
                        sparse_results = await self._vector_store.search_sparse(
                            self._collection_name,
                            SparseVector(
                                indices=sparse_data.indices,
                                values=sparse_data.values,
                            ),
                            # As wide as the dense leg: a caller asking for 100
                            # candidates gets 100 from each side to fuse.
                            overfetch(
                                max(resolved_top_k, self._settings.retrieval_top_k_sparse),
                                collapse,
                            ),
                            qdrant_filters,
                        )
                        sparse_hits = len(sparse_results)
                        if sparse_results:
                            sparse_ranks = {r.id: rank for rank, r in enumerate(sparse_results, 1)}
                            sparse_scores = {r.id: r.score for r in sparse_results}
                            all_search_results.append(sparse_results)
                    except Exception as e:
                        logger.warning("Sparse search failed, continuing with dense only: %s", e)

        if not all_search_results:
            raise RetrievalError("All search attempts failed")

        # --- Step 8: Fusion ---
        from callobuzz_rag.retrieval.fusion import fuse_convex, merge_rrf_multi

        with timings.stage("fuse"):
            if sparse_hits and dense_scores and self._settings.fusion_strategy == "convex":
                # Dense side: each chunk's best cosine over the query variants.
                payloads = {r.id: r.payload for results in all_search_results for r in results}
                fused = fuse_convex(
                    dense_scores,
                    all_search_results[-1],  # the keyword leg is appended last
                    payloads,
                    dense_weight=self._settings.hybrid_dense_weight,
                )
            else:
                fused = merge_rrf_multi(all_search_results, k=self._settings.rrf_k)

        if use_hybrid:
            logger.debug(
                "Hybrid retrieval: %d dense list(s) + %d sparse hit(s) -> %d fused",
                len(all_search_results) - (1 if sparse_hits else 0),
                sparse_hits,
                len(fused),
            )

        if not fused:
            # Search executed (all_search_results is non-empty, checked above) and
            # simply matched nothing in this corpus for this query — a legitimate
            # empty result, not an infrastructure failure. Callers must see a 200
            # with zero chunks so they can apply their own "insufficient evidence"
            # handling instead of treating this as the engine being down.
            logger.debug("No results after fusion for %r", processed_query)

        # One result per distinct passage, before anything ranks or truncates them.
        also_in: dict[str, list[dict[str, str | None]]] = {}
        if collapse and fused:
            fused, also_in = await collapse_duplicate_passages(self._session, fused)

        # Snapshot now: `fused` is rebound by every stage below, and rerank
        # truncates, so after step 11 the fusion score of a dropped chunk is
        # unrecoverable.
        fused_scores: dict[str, float] = {r.id: r.score for r in fused}

        # --- Resolve per-corpus feature toggles (fallback to engine defaults) ---
        use_reranking = (
            enable_reranking if enable_reranking is not None else (self._reranker is not None)
        )
        use_mmr = enable_mmr if enable_mmr is not None else self._settings.mmr_enabled
        use_temporal_decay = (
            enable_temporal_decay
            if enable_temporal_decay is not None
            else self._settings.temporal_decay_enabled
        )

        # --- Load chunk data from DB if needed by steps 9-11 ---
        needs_chunk_data = (
            (use_reranking and self._reranker is not None) or use_mmr or use_temporal_decay
        )
        chunk_data: dict[str, tuple[str, datetime | None]] = {}
        if needs_chunk_data and fused:
            with timings.stage("load_chunk_text"):
                chunk_data = await self._load_chunk_data([r.id for r in fused])

        # When on, every post-fusion stage writes its own score onto
        # `SearchResult.score`, and assembly is told to trust the list order
        # instead of re-sorting by it. Off, `score` stays the RRF fusion score
        # and the historical (wrong) ordering is preserved bit for bit.
        use_pipeline_order = (
            enable_pipeline_order
            if enable_pipeline_order is not None
            else self._settings.pipeline_order_enabled
        )

        # --- Step 9: Cross-encoder reranking (optional) ---
        rerank_scores: dict[str, float] = {}
        if use_reranking and self._reranker is not None and fused:
            with timings.stage("rerank"):
                fused, rerank_scores = await self._rerank_fused(fused, processed_query, chunk_data)
                if use_pipeline_order:
                    fused = _write_scores_back(fused, rerank_scores)

        # --- Step 10: MMR diversity filtering (optional) ---
        #
        # No write-back: MMR orders by marginal relevance, not by a score it
        # could put on the result. This is the stage that makes `preserve_order`
        # necessary rather than merely tidy.
        if use_mmr and fused:
            with timings.stage("mmr"):
                fused = self._apply_mmr(fused, chunk_data)

        # --- Step 11: Temporal decay scoring (optional) ---
        decay_results: dict[str, DecayResult] = {}
        if use_temporal_decay and fused:
            with timings.stage("temporal_decay"):
                fused, decay_results = self._apply_temporal_decay(fused, chunk_data)
                if use_pipeline_order:
                    fused = _write_scores_back(
                        fused,
                        {cid: d.decayed_score for cid, d in decay_results.items()},
                    )

        # --- Step 12: Caller's category policy (optional) ---
        #
        # Applied BEFORE the trim, so a weight can pull a chunk into the answer or
        # push one out of it — weighting only what already made the cut would make
        # this cosmetic. It is deliberately the last word: the caller knows what
        # its user is doing, and the engine does not.
        applied_weights: dict[str, float] = {}
        if category_weights and fused:
            with timings.stage("category_weights"):
                fused, applied_weights = self._apply_category_weights(fused, category_weights)

        # Trim to final_k
        top_results = fused[:resolved_final_k]

        score_breakdown: dict[str, ChunkScoreBreakdown] = {}
        for position, r in enumerate(top_results, 1):
            decay = decay_results.get(r.id)
            score_breakdown[r.id] = ChunkScoreBreakdown(
                fused_score=fused_scores.get(r.id, r.score),
                pipeline_rank=position,
                category=r.payload.get("category") if r.payload else None,
                category_weight=applied_weights.get(r.id),
                dense_rank=dense_ranks.get(r.id),
                sparse_rank=sparse_ranks.get(r.id),
                dense_score=dense_scores.get(r.id),
                sparse_score=sparse_scores.get(r.id),
                rerank_score=rerank_scores.get(r.id),
                decay_multiplier=decay.multiplier if decay is not None else None,
                age_days=decay.age_days if decay is not None else None,
            )

        # --- Step 13: Assemble context + prompt ---
        context_results = [
            ContextSearchResult(
                id=uuid.UUID(r.id),
                score=r.score,
            )
            for r in top_results
        ]
        assemble_kwargs: dict[str, Any] = {
            "preserve_order": use_pipeline_order,
            "token_budget": context_budget,
        }
        if system_prompt is not None:
            assemble_kwargs["system_prompt"] = system_prompt
        if constraints is not None:
            assemble_kwargs["constraints"] = constraints
        if response_language is not None:
            assemble_kwargs["response_language"] = response_language
        if updated_tz is not None:  # TV5 freshness
            assemble_kwargs["updated_tz"] = updated_tz

        with timings.stage("assemble"):
            assembled = await self._assembler.assemble(
                self._session,
                context_results,
                processed_query,
                **assemble_kwargs,
            )

        # One line per retrieval, at INFO. This is the whole point: the next latency
        # question gets answered from a log of real traffic instead of by re-running one query six times with a stage
        # switched off and subtracting.
        stage_timings = timings.as_dict()
        logger.info(
            "Retrieval stages (%dms total): %s",
            sum(stage_timings.values()),
            timings.summary(),
        )

        return _RetrievalPipelineResult(
            processed_query=processed_query,
            assembled=assembled,
            plan=plan,
            top_results=top_results,
            score_breakdown=score_breakdown,
            stage_timings_ms=stage_timings,
            also_in={r.id: also_in[r.id] for r in top_results if r.id in also_in},
            queries=list(all_queries),
        )

    @trace("query_pipeline.query")
    async def query(
        self,
        text: str,
        corpus_id: uuid.UUID,
        top_k: int | None = None,
        final_k: int | None = None,
        enable_hyde: bool | None = None,
        enable_classification: bool | None = None,
        enable_validation: bool | None = None,
        enable_mmr: bool | None = None,
        enable_reranking: bool | None = None,
        enable_temporal_decay: bool | None = None,
        enable_hybrid: bool | None = None,
        keyword_only: bool = False,
        enable_pipeline_order: bool | None = None,
        system_prompt: str | None = None,
        constraints: str | None = None,
        response_language: str | None = None,
        category: str | None = None,
        category_weights: dict[str, float] | None = None,
        user_id: str | None = None,
        metadata_filters: Mapping[str, str | list[str]] | None = None,
        answer_llm: LLMProvider | None = None,
    ) -> dict[str, Any]:
        """Execute the full query pipeline.

        Returns:
            Dict with answer, citations, confidence, query_id, model, latency.

        Raises:
            QueryError: If query preprocessing or generation fails.
            RetrievalError: If search or reranking fails.
        """
        query_id = uuid.uuid4()
        t_start = time.monotonic()

        try:
            # Steps 1-13: Shared retrieval pipeline
            pipeline = await self._run_retrieval_pipeline(
                text,
                corpus_id,
                # Keyword-only from here down. These used to be positional, which
                # made inserting `final_k` into the signature silently rebind
                # `enable_hyde` to it — a type-correct, test-passing disaster.
                top_k=top_k,
                final_k=final_k,
                enable_hyde=enable_hyde,
                enable_classification=enable_classification,
                enable_mmr=enable_mmr,
                enable_reranking=enable_reranking,
                enable_temporal_decay=enable_temporal_decay,
                enable_hybrid=enable_hybrid,
                keyword_only=keyword_only,
                enable_pipeline_order=enable_pipeline_order,
                system_prompt=system_prompt,
                constraints=constraints,
                response_language=response_language,
                category=category,
                category_weights=category_weights,
                user_id=user_id,
                metadata_filters=metadata_filters,
            )
            assembled = pipeline.assembled

            # --- Step 14: LLM generation ---
            try:
                llm_response = await (answer_llm or self._llm).complete(assembled.messages)
            except Exception as e:
                raise QueryError(f"LLM generation failed: {e}") from e

            answer = llm_response.content
            model_used = llm_response.model

            # --- Step 15: Validation ---
            use_validation = (
                enable_validation
                if enable_validation is not None
                else self._settings.validation_enabled
            )
            confidence = 1.0

            if use_validation:
                chunk_texts = [cc.llm_text for cc in assembled.cited_chunks]
                confidence = await self._run_validation(
                    pipeline.processed_query, answer, chunk_texts
                )

            # --- Step 16: Build citations ---
            citations = [
                {
                    "chunk_id": str(cc.chunk_id),
                    "document_id": str(cc.document_id) if cc.document_id else None,
                    "external_id": cc.external_id,
                    "section": cc.section,
                    "file_name": cc.file_name,
                    "text_snippet": (cc.text[:200] + "..." if len(cc.text) > 200 else cc.text),
                    "page": cc.page,
                    "score": cc.score,
                    "also_in": pipeline.also_in.get(str(cc.chunk_id), []),
                }
                for cc in assembled.cited_chunks
            ]

            # --- Log to DB ---
            latency_ms = int((time.monotonic() - t_start) * 1000)
            token_usage: dict[str, Any] = {
                "prompt_tokens": llm_response.usage.prompt_tokens,
                "completion_tokens": llm_response.usage.completion_tokens,
                "total_tokens": llm_response.usage.total_tokens,
            }
            # Present only when the provider reports thinking tokens (a reasoning model).
            # They are already inside completion_tokens; this is how many of them.
            if llm_response.usage.reasoning_tokens > 0:
                token_usage["reasoning_tokens"] = llm_response.usage.reasoning_tokens
            # What the whole request cost, helper calls included: `cost_usd` (None and
            # `cost_known` false when a call's price is unknown), `reasoning_tokens`.
            spent = request_cost_of(self._llm)
            if spent is not None:
                token_usage.update(spent.as_dict())
            await self._log_query(
                query_id=query_id,
                corpus_id=corpus_id,
                query_text=pipeline.processed_query,
                chunk_ids=[cc.chunk_id for cc in assembled.cited_chunks],
                response=answer,
                confidence=confidence,
                latency_ms=latency_ms,
                model_used=model_used,
                token_usage=dict(token_usage),
            )

            return {
                "answer": answer,
                "citations": citations,
                "confidence": confidence,
                "query_id": query_id,
                "model_used": model_used,
                "latency_ms": latency_ms,
                "token_usage": token_usage,
            }

        except (QueryError, RetrievalError):
            raise

        except Exception as e:
            logger.error("Query pipeline failed: %s", e, exc_info=True)
            raise QueryError(f"Query pipeline failed: {e}") from e

    @trace("query_pipeline.query_stream")
    async def query_stream(
        self,
        text: str,
        corpus_id: uuid.UUID,
        top_k: int | None = None,
        final_k: int | None = None,
        enable_hyde: bool | None = None,
        enable_classification: bool | None = None,
        system_prompt: str | None = None,
        constraints: str | None = None,
        response_language: str | None = None,
        category: str | None = None,
        category_weights: dict[str, float] | None = None,
        user_id: str | None = None,
        metadata_filters: Mapping[str, str | list[str]] | None = None,
    ) -> AsyncIterator[str]:
        """Execute query pipeline and stream the LLM response.

        Runs steps 1-13 via shared pipeline, then streams step 14.
        Validation is skipped (requires full text).
        """
        # Steps 1-13: Shared retrieval pipeline
        pipeline = await self._run_retrieval_pipeline(
            text,
            corpus_id,
            top_k=top_k,
            final_k=final_k,
            enable_hyde=enable_hyde,
            enable_classification=enable_classification,
            system_prompt=system_prompt,
            constraints=constraints,
            response_language=response_language,
            category=category,
            category_weights=category_weights,
            user_id=user_id,
            metadata_filters=metadata_filters,
        )

        # Step 14: Stream LLM response
        async for chunk in self._llm.complete_stream(pipeline.assembled.messages):
            yield chunk

    async def _load_chunk_data(
        self, chunk_ids: list[str]
    ) -> dict[str, tuple[str, datetime | None]]:
        """Load each chunk's index text and created_at for reranking/MMR/decay.

        The cross-encoder reads the same heading-prefixed text the chunk was
        embedded with, so it too knows which section a chunk came from.
        """
        from callobuzz_rag.chunking.index_text import index_text

        if not chunk_ids:
            return {}
        uuids = [uuid.UUID(cid) for cid in chunk_ids]
        stmt = select(Chunk.id, Chunk.text, Chunk.structure_path, Chunk.created_at).where(
            Chunk.id.in_(uuids)
        )
        result = await self._session.execute(stmt)
        return {
            str(row.id): (index_text(row.text, row.structure_path), row.created_at)
            for row in result.all()
        }

    async def _rerank_fused(
        self,
        fused: list[Any],
        query: str,
        chunk_data: dict[str, tuple[str, datetime | None]],
    ) -> tuple[list[Any], dict[str, float]]:
        """Step 9: Cross-encoder reranking on fused results.

        Returns the reordered results and each survivor's cross-encoder score.
        The score is not written back here — the caller does that, and only when
        `pipeline_order_enabled` is on. With it off, `SearchResult.score`
        stays the fusion score all the way to the caller and this mapping is the
        only way anything downstream can see what the reranker actually thought.
        """
        assert self._reranker is not None
        # Only the top `rerank_top_n_input` fused candidates go to the cross-encoder.
        # The setting existed but was never applied, so a top_k=100 hybrid query
        # scored every fused candidate — up to ~200 pairs instead of 50.
        candidates = fused[: self._settings.rerank_top_n_input]
        documents = [chunk_data.get(r.id, ("", None))[0] for r in candidates]
        # Skip if no text available
        if not any(documents):
            logger.warning("No chunk text for reranking, skipping")
            return fused, {}
        try:
            reranked = await self._reranker.rerank(
                query=query,
                documents=documents,
                top_k=self._settings.rerank_top_k_output,
            )
            return (
                [candidates[rr.index] for rr in reranked],
                {candidates[rr.index].id: rr.score for rr in reranked},
            )
        except Exception as e:
            logger.warning("Reranking failed, using fusion order: %s", e)
            return fused, {}

    def _apply_mmr(
        self,
        fused: list[Any],
        chunk_data: dict[str, tuple[str, datetime | None]],
    ) -> list[Any]:
        """Step 10: MMR diversity filtering using DB-loaded text."""
        from callobuzz_rag.retrieval.mmr import MMRItem, mmr_rerank

        mmr_items = [
            MMRItem(
                id=r.id,
                score=r.score,
                content=chunk_data.get(r.id, ("", None))[0],
            )
            for r in fused
        ]
        # If no text in any item, skip MMR (needs content for similarity)
        if not any(item.content for item in mmr_items):
            return fused
        mmr_results = mmr_rerank(mmr_items, lambda_=self._settings.mmr_lambda)
        fused_lookup = {r.id: r for r in fused}
        return [fused_lookup[m.id] for m in mmr_results if m.id in fused_lookup]

    @staticmethod
    def _apply_category_weights(
        fused: list[Any],
        category_weights: dict[str, float],
    ) -> tuple[list[Any], dict[str, float]]:
        """Step 12: scale scores by the caller's per-category trust, then re-sort.

        Unlike every other post-fusion stage, this one WRITES THE SCORE BACK. It has
        to: the engine's own context assembler re-sorts by `score`, so a stage that
        only reorders has its work undone before the caller ever sees it.
        Writing the score is also the honest thing here — the number is no longer a
        pure RRF score once a policy has been applied to it, and `score_breakdown`
        carries the unweighted `fused_score` alongside so nothing is lost.

        A category the caller did not name, and a chunk with no category, weigh 1.0.
        An omission is "no opinion", never a demotion.

        The base being scaled is whatever `score` currently holds, which is the
        point of the write-back: with `pipeline_order_enabled` ON that is the
        decayed cross-encoder score, so a weight scales the ranking the pipeline
        actually arrived at and the re-sort agrees with it. With it OFF it is
        still the raw fusion score, so applying any weight re-sorts by fusion
        score and discards the reranker's ordering — which cost nothing while
        that ordering was being discarded downstream anyway.

        MMR is the exception either way: it orders for diversity, so any re-sort
        here overrides it. That is deliberate — the caller's policy is the last
        word — but it means a category policy and MMR genuinely do fight, and
        the policy wins.

        Returns the re-sorted results and the weight actually applied to each.
        """
        from dataclasses import replace

        weighted: list[Any] = []
        applied: dict[str, float] = {}
        for r in fused:
            category = r.payload.get("category") if r.payload else None
            weight = category_weights.get(category, 1.0) if category else 1.0
            applied[r.id] = weight
            weighted.append(replace(r, score=r.score * weight) if weight != 1.0 else r)

        # If nothing was actually weighted, change NOTHING. Sorting unconditionally
        # would re-rank the pool by fusion score and silently discard the order the
        # pipeline chose — so sending a policy about a category this query did not
        # match would alter the answer. Verified live: `{"product": 3.0}` on a pool
        # holding no product chunks returned a different five.
        if all(w == 1.0 for w in applied.values()):
            return fused, applied

        weighted.sort(key=lambda r: r.score, reverse=True)
        return weighted, applied

    def _apply_temporal_decay(
        self,
        fused: list[Any],
        chunk_data: dict[str, tuple[str, datetime | None]],
    ) -> tuple[list[Any], dict[str, DecayResult]]:
        """Step 11: Temporal decay scoring using DB-loaded timestamps.

        Returns the reordered results and the per-chunk decay detail. As with
        reranking, the caller writes `decayed_score` back only when
        `pipeline_order_enabled` is on; otherwise it orders this list and
        is then discarded, so the multiplier has to travel separately.
        """
        from datetime import UTC

        from callobuzz_rag.retrieval.temporal_decay import (
            apply_decay_to_results,
        )

        decay_items: list[tuple[str, float, datetime]] = []
        for r in fused:
            data = chunk_data.get(r.id)
            if data and data[1] is not None:
                created_at = data[1]
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=UTC)
                decay_items.append((r.id, r.score, created_at))

        if not decay_items:
            return fused, {}

        decayed = apply_decay_to_results(
            decay_items,
            half_life_days=self._settings.temporal_decay_half_life_days,
        )
        decay_order = {d.id: i for i, d in enumerate(decayed)}
        return (
            sorted(fused, key=lambda r: decay_order.get(r.id, len(fused))),
            {d.id: d for d in decayed},
        )

    async def _build_query_plan(
        self,
        query: str,
        enable_hyde: bool = False,
    ) -> QueryPlan:
        """Build a QueryPlan via classification, decomposition, and variants."""
        from callobuzz_rag.retrieval.query_understanding import (
            QueryClassifier,
            SubQuestionDecomposer,
        )
        from callobuzz_rag.retrieval.query_variants import (
            HyDEGenerator,
            MultiQueryGenerator,
        )

        classifier = QueryClassifier(self._llm)
        decomposer = SubQuestionDecomposer(self._llm)
        variant_gen = MultiQueryGenerator(self._llm, count=self._settings.multi_query_count)

        # Classify
        complexity = await classifier.classify(query)

        plan = QueryPlan(complexity=complexity, original_query=query)

        # Decompose if complex
        if plan.needs_decomposition:
            plan.sub_queries = await decomposer.decompose(query, complexity)

        # Generate variants (always, for broader recall)
        plan.variants = await variant_gen.generate(query)

        # HyDE (optional, for complex queries)
        if enable_hyde and plan.needs_decomposition:
            hyde_gen = HyDEGenerator(self._llm)
            plan.hyde_text = await hyde_gen.generate(query)

        return plan

    async def _run_validation(
        self,
        query: str,
        response: str,
        context_chunks: list[str],
    ) -> float:
        """Run the 3-stage validation pipeline, return confidence score."""
        from callobuzz_rag.retrieval.validation import (
            Auditor,
            Gatekeeper,
            Strategist,
            ValidationPipeline,
        )

        pipeline = ValidationPipeline(
            gatekeeper=Gatekeeper(self._llm),
            auditor=Auditor(self._llm),
            strategist=Strategist(self._llm),
            gatekeeper_weight=(self._settings.validation_gatekeeper_weight),
            auditor_weight=self._settings.validation_auditor_weight,
            strategist_weight=(self._settings.validation_strategist_weight),
            confidence_threshold=(self._settings.validation_confidence_threshold),
        )

        try:
            result = await pipeline.validate(query, response, context_chunks)
            return result.confidence
        except Exception as e:
            logger.warning("Validation failed, defaulting to 1.0: %s", e)
            return 1.0

    async def _log_query(
        self,
        query_id: uuid.UUID,
        query_text: str,
        chunk_ids: list[uuid.UUID],
        response: str,
        confidence: float,
        latency_ms: int,
        model_used: str,
        token_usage: dict[str, Any],
        corpus_id: uuid.UUID | None = None,
    ) -> None:
        """Persist query log to database."""
        try:
            current_trace = get_current_trace()
            log = QueryLog(
                id=query_id,
                corpus_id=corpus_id,
                query_text=query_text,
                retrieved_chunk_ids=chunk_ids,
                response=response,
                confidence_score=confidence,
                latency_ms=latency_ms,
                model_used=model_used,
                token_usage=token_usage,
                trace_id=getattr(current_trace, "id", None) if current_trace else None,
            )
            self._session.add(log)
            await self._session.flush()
        except Exception as e:
            logger.error("Failed to log query %s: %s", query_id, e)
