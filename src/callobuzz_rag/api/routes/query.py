"""Query endpoints -- synchronous query execution.

Phase 4: Multi-corpus support. Corpus derived from auth middleware,
config resolved via 3-tier chain (request -> corpus -> engine defaults).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from callobuzz_rag.accounts.caps import counts_cached_answers
from callobuzz_rag.analytics.usage import (
    SOURCE_API,
    SOURCE_STREAM,
    estimate_tokens,
    payer_of,
    record_usage,
    record_usage_new_session,
    request_cost_of,
)
from callobuzz_rag.api.deps import authorized_corpus_ids, get_session, resolve_scope
from callobuzz_rag.api.errors import APIError  # PIP1 lane
from callobuzz_rag.api.schemas.query import (
    QueryHistoryResponse,
    QueryRequest,
    QueryResponse,
    RetrievedChunk,
    RetrieveRequest,
    RetrieveResponse,
    ScoreBreakdown,
)
from callobuzz_rag.api.stage_events import (
    STAGE_UNDERSTAND,
    STAGE_WRITE,
    StageClock,
    events_until_done,
    public_timings,
    stage_listener,
    stop_search,
)
from callobuzz_rag.api.streaming import shielded, sse_error, sse_event, sse_token
from callobuzz_rag.billing.context import mark_admin_answer  # BIL3 request id
from callobuzz_rag.conversation import surfaces
from callobuzz_rag.core.exceptions import QueryError, RetrievalError
from callobuzz_rag.db.models.query_log import QueryLog
from callobuzz_rag.fairuse.alerts import schedule_busy  # PIP3 gate
from callobuzz_rag.fairuse.gate import answer_gate, pause_args, refuse_paused  # PIP3 gate
from callobuzz_rag.llm.language_routing import route_answer_llm
from callobuzz_rag.llm.stream_stats import collect_stream_stats

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.core.config_resolver import (
        ConfigResolver,
        ResolvedLLMConfig,
        ResolvedPromptConfig,
        ResolvedRetrievalConfig,
    )
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.llm.base import LLMProvider
    from callobuzz_rag.retrieval.orchestrator import QueryOrchestrator
    from callobuzz_rag.retrieval.query_cache import QueryResponseCache
    from callobuzz_rag.retrieval.reranking.base import RerankProvider
    from callobuzz_rag.tiers.schema import EffectiveProfile  # PIP1 profile
    from callobuzz_rag.tiers.slot import SlotHold  # PIP1 lane

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/query", tags=["query"])


def _resolve_corpus(
    request: Request,
    body: QueryRequest,
) -> Corpus | None:
    """Resolve the target corpus from auth state and request body.

    Returns Corpus if resolved from auth key, None if admin/tenant must look up by ID.
    """
    auth_corpus: Corpus | None = getattr(request.state, "corpus", None)
    is_admin: bool = getattr(request.state, "is_admin", False)
    tenant = getattr(request.state, "tenant", None)

    if auth_corpus is not None:
        if body.corpus_id is not None and body.corpus_id != auth_corpus.id:
            raise HTTPException(
                status_code=403,
                detail="corpus_id does not match authenticated corpus",
            )
        return auth_corpus

    # Tenant key auth — requires corpus_id, ownership verified after DB lookup
    if tenant is not None:
        if body.corpus_id is None:
            raise HTTPException(
                status_code=422,
                detail="corpus_id is required when using a tenant API key",
            )
        return None  # Caller must look up by ID

    if is_admin:
        if body.corpus_id is None:
            raise HTTPException(
                status_code=422,
                detail="corpus_id is required when using an admin API key",
            )
        return None

    raise HTTPException(status_code=401, detail="Not authenticated")


async def _get_corpus_by_id(session: AsyncSession, corpus_id: UUID) -> Corpus:
    """Load corpus from DB by ID. Raises 404 if not found."""
    from callobuzz_rag.db.models.corpus import Corpus

    corpus = await session.get(Corpus, corpus_id)
    if not corpus:
        raise HTTPException(status_code=404, detail="Corpus not found")
    return corpus


def _select_reranker(
    local: RerankProvider | None,
    llm: LLMProvider,
    resolved_retrieval: ResolvedRetrievalConfig | None,
    *,
    settings: Any = None,
) -> RerankProvider | None:
    """Pick the reranker for this request.

    `local` is the startup singleton — the cross-encoder, loaded once, free, and
    slow. `llm` ranks on the caller's own credentials: faster, and billed to
    them. The choice is the corpus's, resolved upstream.

    Two rules, both about failing in the cheap and quiet direction:

    - **Paid never runs without a credential.** `LLMRerankProvider.usable` is
      checked here rather than discovered as an exception mid-pipeline, because
      the orchestrator catches rerank failures and continues with the raw fusion
      order — so an unusable paid reranker would present as reranking silently
      switched off.
    - **Paid always has the local one behind it.** A provider outage or an
      expired key then costs latency, not ranking quality. Without the wrapper
      the same failure is invisible.
    """
    from callobuzz_rag.retrieval.reranking.llm import (
        FallbackRerankProvider,
        LLMRerankProvider,
    )

    # PIP1 profile
    if resolved_retrieval is not None and resolved_retrieval.rerank_provider == "api":
        return _select_api_reranker(local, resolved_retrieval, settings)
    # /PIP1 profile
    if resolved_retrieval is None or resolved_retrieval.rerank_provider != "llm":
        return local

    paid = LLMRerankProvider(llm, model=resolved_retrieval.rerank_llm_model)
    if not paid.usable:
        logger.warning(
            "Corpus asked for the LLM reranker but the request carries no LLM "
            "credential; using the local cross-encoder"
        )
        return local
    if local is None:
        return paid
    return FallbackRerankProvider(paid, local)


def _select_api_reranker(
    local: RerankProvider | None,
    resolved_retrieval: ResolvedRetrievalConfig,
    settings: Any,
) -> RerankProvider | None:
    """The hosted reranking service (PIP1 profile `reranker: api`), behind the local one.

    Without `RAG_RERANK_API_KEY` or a rerank model the service cannot run, so the local
    cross-encoder answers (with a warning): a missing key costs latency, never the ranking.
    """
    from callobuzz_rag.retrieval.reranking.api import APIRerankProvider
    from callobuzz_rag.retrieval.reranking.llm import FallbackRerankProvider

    key = settings.rerank_api_key.get_secret_value() if settings is not None else ""
    paid = APIRerankProvider(resolved_retrieval.rerank_llm_model or "", key)
    if not paid.usable:
        logger.warning(
            "A profile asked for the hosted reranker but RAG_RERANK_API_KEY or the rerank "
            "model is not set; using the local cross-encoder"
        )
        return local
    if local is None:
        return paid
    return FallbackRerankProvider(paid, local)


def _build_orchestrator(
    request: Request,
    session: AsyncSession,
    corpus: Corpus,
    resolved_llm: ResolvedLLMConfig,
    resolved_prompt: ResolvedPromptConfig,
    *,
    resolved_retrieval: ResolvedRetrievalConfig | None = None,
    llm: LLMProvider | None = None,
) -> QueryOrchestrator:
    """Build QueryOrchestrator with per-corpus providers.

    `resolved_retrieval` is keyword-only on purpose: inserting a positional
    parameter into this signature silently rebound an argument at two call sites
    the last time it happened, and every test still passed.

    `llm` is the request's own provider when the caller already holds one (the
    conversation director shares ONE provider across the label, the translation, retrieval
    and the answer, so a single cost ledger holds the whole turn); without it a fresh
    provider is built from `resolved_llm`.
    """
    from callobuzz_rag.llm.litellm_provider import LiteLLMProvider
    from callobuzz_rag.retrieval.orchestrator import QueryOrchestrator

    settings = request.app.state.settings
    if request.app.state.embedder is None or request.app.state.context_assembler is None:
        raise HTTPException(
            status_code=503,
            detail="Query API unavailable: Phase 2 providers not initialized",
        )

    # fast_model resolution now lives in ConfigResolver (corpus -> engine default),
    # and from_resolved_config falls back to the main model when it is unset — so
    # the private-attribute patch that used to sit here is no longer needed.
    per_request_llm = llm or LiteLLMProvider.from_resolved_config(resolved_llm)

    # The orchestrator reads its numeric knobs from settings; the corpus's own values
    # have to be in the copy it gets, or a corpus-level rrf_k / mmr_lambda / fusion
    # choice is resolved and then silently ignored — as it was.
    if resolved_retrieval is not None:
        settings = settings.model_copy(
            update={
                "fusion_strategy": resolved_retrieval.fusion_strategy,
                "hybrid_dense_weight": resolved_retrieval.hybrid_dense_weight,
                "rrf_k": resolved_retrieval.rrf_k,
                "mmr_lambda": resolved_retrieval.mmr_lambda,
                "collapse_duplicates": resolved_retrieval.collapse_duplicates,
            }
        )

    from callobuzz_rag.embeddings.factory import embedder_for_corpus

    # The corpus's own model when its collection holds one (after a migration, or
    # chosen at create); the engine's embedder otherwise.
    embedder = embedder_for_corpus(corpus, request.app.state.settings, request.app.state.embedder)

    return QueryOrchestrator(
        session=session,
        settings=settings,
        embedder=embedder,
        vector_store=request.app.state.vector_store,
        llm_provider=per_request_llm,
        context_assembler=request.app.state.context_assembler,
        reranker=_select_reranker(
            getattr(request.app.state, "reranker", None),
            per_request_llm,
            resolved_retrieval,
            settings=request.app.state.settings,  # PIP1 profile
        ),
        collection_name=corpus.qdrant_collection,
    )


def _get_config_resolver(request: Request) -> ConfigResolver:
    """Get ConfigResolver from app state."""
    from callobuzz_rag.core.config_resolver import ConfigResolver

    resolver = getattr(request.app.state, "config_resolver", None)
    if resolver is None:
        settings = request.app.state.settings
        encryptor = getattr(request.app.state, "encryptor", None)
        if encryptor is None:
            # An app built without the lifespan (tests, embedding) has no state
            # encryptor; the startup check that validates the key did not run either.
            from callobuzz_rag.core.secrets import EncryptionError, encryptor_from_settings

            try:
                encryptor = encryptor_from_settings(settings)
            except EncryptionError:
                logger.warning("RAG_ENCRYPTION_KEY is invalid; corpus-stored keys unreadable")
        resolver = ConfigResolver(settings, encryptor=encryptor)
    return resolver


def _extract_llm_headers(request: Request) -> dict[str, str]:
    """Extract X-LLM-* headers from the request for config resolution.

    The full per-request LLM contract. A caller that sends all of these needs no
    stored credentials in the engine at all:

        X-LLM-Provider: openrouter          # LiteLLM provider id
        X-LLM-Model:    anthropic/claude-3.5-haiku
        X-LLM-API-Key:  <the caller's own key>
        X-LLM-Base-URL: https://...         # optional; self-hosted/proxy targets
    """
    headers: dict[str, str] = {}
    for key in ("x-llm-provider", "x-llm-model", "x-llm-api-key", "x-llm-base-url"):
        value = request.headers.get(key)
        if value:
            headers[key] = value
    return headers


def _get_query_cache(request: Request) -> QueryResponseCache:
    """Get or create QueryResponseCache from app state."""
    cache = getattr(request.app.state, "query_cache", None)
    if cache is None:
        from callobuzz_rag.retrieval.query_cache import QueryResponseCache

        cache = QueryResponseCache(request.app.state.settings)
        request.app.state.query_cache = cache
    return cache


async def _resolve_for(
    request: Request,
    session: AsyncSession,
    resolver: ConfigResolver,
    corpus: Corpus,
    *,
    headers: dict[str, str] | None,
    profile: EffectiveProfile | None = None,
) -> tuple[ResolvedLLMConfig, ResolvedRetrievalConfig, EffectiveProfile]:
    """The knowledge base's LLM and retrieval configuration under the business's pipeline
    profile (PIP1). `profile` is given by a caller that already decided it (the fair-use gate);
    otherwise it is the business's plan profile."""
    from callobuzz_rag.tiers import profiles
    from callobuzz_rag.tiers.resolve import resolve_with_profile

    settings = request.app.state.settings
    if profile is None:
        profile = await profiles.plan_profile(session, corpus.tenant_id, settings=settings)
    llm, retrieval = resolve_with_profile(
        resolver, corpus, headers=headers, profile=profile, settings=settings
    )
    return llm, retrieval, profile


# PIP3 gate
async def gate_and_resolve(
    request: Request,
    session: AsyncSession,
    resolver: ConfigResolver,
    corpus: Corpus,
    llm: ResolvedLLMConfig,
    retrieval: ResolvedRetrievalConfig,
    profile: EffectiveProfile,
    *,
    headers: dict[str, str] | None,
    surface: str = "api",
    enforce_cap: bool = True,
    count_question: bool = True,
) -> tuple[Any, ResolvedLLMConfig, ResolvedRetrievalConfig, EffectiveProfile]:
    """The one gate every answering route passes (monthly cap, billing, fair use). Returns the
    decision and the configuration to answer with: unchanged when the decision allows, resolved
    again under the fallback profile when it downgrades. A pause leaves the configuration alone
    (the route answers with the pause reply, or refuses on the legacy paths)."""
    decision = await answer_gate(
        session,
        request,
        corpus,
        payer=payer_of(llm),
        surface=surface,  # type: ignore[arg-type]
        enforce_cap=enforce_cap,
        count_question=count_question,
        plan_profile=profile,
    )
    if decision.action == "downgrade":
        llm, retrieval, profile = await _resolve_for(
            request, session, resolver, corpus, headers=headers, profile=decision.profile
        )
    elif decision.action == "allow":
        profile = decision.profile
    return decision, llm, retrieval, profile


# /PIP3 gate


async def hold_slot_or_busy(
    request: Request, profile: EffectiveProfile, tenant_id: UUID
) -> SlotHold:
    """Take the answer slot for a legacy (assistant off) answer, or raise 503 `answers_busy`.

    The caller releases the hold in a `finally`. No lane applies (the common case) means the
    hold is empty and nothing is waited for."""
    from callobuzz_rag.tiers.slot import SlotHold, slot_spec

    hold = SlotHold(slot_spec(request.app, profile), tenant_id)
    try:
        async for _waited in hold.wait():
            pass
    except BaseException:
        await hold.release()
        raise
    if hold.busy:
        schedule_busy(  # PIP3 gate: counted, and given back to the question windows
            getattr(request.app.state, "session_factory", None),
            tenant_id,
            redis=getattr(request.app.state, "fair_use_redis", None),
        )
        raise APIError(
            503,
            "The engine is answering as many questions as it can right now. "
            "Please try again in a minute.",
            "answers_busy",
            headers={"Retry-After": "30"},
        )
    return hold


async def slotted_stream(
    request: Request,
    profile: EffectiveProfile,
    tenant_id: UUID,
    inner: AsyncIterator[str],
    *,
    busy_event: Callable[[], str] | None = None,
) -> AsyncIterator[str]:
    """`inner` (an answer's SSE frames) behind the answer slot.

    While the question waits the client gets one `stage: queued` frame; a wait that ends in a
    timeout becomes one `error` frame (code `answers_busy`) or whatever `busy_event` builds
    (the widget turns it into its own wording), because a stream has already sent its 200. The
    slot is released however the stream ends, a client leaving mid-answer included."""
    from callobuzz_rag.tiers.slot import SlotHold, slot_spec

    hold = SlotHold(slot_spec(request.app, profile), tenant_id)
    try:
        async for frame in _queued_frames(hold):
            yield frame
        if hold.busy:
            schedule_busy(  # PIP3: counted, and given back to the question windows
                getattr(request.app.state, "session_factory", None),
                tenant_id,
                redis=getattr(request.app.state, "fair_use_redis", None),
            )
            await inner.aclose()  # type: ignore[attr-defined]
            yield (
                busy_event()
                if busy_event is not None
                else sse_event(
                    "error",
                    {
                        "detail": "The engine is answering as many questions as it can right "
                        "now. Please try again in a minute.",
                        "code": "answers_busy",
                    },
                )
            )
            return
        async for frame in inner:
            yield frame
    finally:
        await hold.release()


async def _queued_frames(hold: SlotHold) -> AsyncIterator[str]:
    """The `stage: queued` SSE frame, once, while a question waits for its turn."""
    from callobuzz_rag.api.stage_events import STAGE_QUEUED

    sent = False
    async for waited_ms in hold.wait():
        if not sent:
            sent = True
            yield sse_event("stage", {"stage": STAGE_QUEUED, "elapsed_ms": waited_ms})


async def _meter_failed_stream(
    session_factory: Any,
    corpus: Corpus,
    llm: Any,
    model: str,
    payer: str,
    request_id: uuid.UUID | None = None,
) -> None:
    """Record what a stream spent before it failed, when the provider's ledger holds anything:
    tokens and cost, but not another answered question."""
    spent = request_cost_of(llm)
    if spent is None:
        return
    await shielded(
        record_usage_new_session(
            session_factory,
            corpus_id=corpus.id,
            tenant_id=corpus.tenant_id,
            source=SOURCE_STREAM,
            prompt_tokens=0,
            completion_tokens=0,
            model=model,
            cost=spent,
            payer=payer,
            requests=0,
            request_id=request_id,  # BIL3 request id
        )
    )


@router.post("", response_model=QueryResponse)
async def execute_query(
    request: Request,
    body: QueryRequest,
    session: AsyncSession = Depends(get_session),
) -> QueryResponse:
    """Execute a query against the corpus and return a generated answer."""
    corpus = _resolve_corpus(request, body)
    mark_admin_answer(request)  # BIL3 request id: the operator's own tests are not billed
    if corpus is None:
        corpus = await _get_corpus_by_id(session, body.corpus_id)  # type: ignore[arg-type]

    # Tenant ownership check
    tenant = getattr(request.state, "tenant", None)
    if tenant is not None and corpus.tenant_id != tenant.id:
        raise HTTPException(status_code=403, detail="Corpus does not belong to this tenant")

    resolver = _get_config_resolver(request)

    # Resolved before the cache is asked: even a cached answer is metered under whoever would
    # have paid for it (the engine's key or the knowledge base's own).
    # PIP1 profile: the business's pipeline profile is laid over the knowledge base's settings
    resolved_llm, resolved_retrieval, profile = await _resolve_for(
        request, session, resolver, corpus, headers=_extract_llm_headers(request) or None
    )
    # /PIP1 profile

    # Conversation layer (C1): the director answers whenever the knowledge base's assistant is
    # on (or a draft is previewed). It bypasses the response cache (the cached shape has no
    # envelope and is keyed without the assistant's version) and meters its own real cost.
    surfaces.authorize_assistant_options(request, body.assistant)
    runtime = await surfaces.assistant_for(
        session, corpus, use_draft=bool(body.assistant and body.assistant.use_draft)
    )
    llm_headers = _extract_llm_headers(request) or None  # PIP3 gate
    if surfaces.is_human_turn(runtime, body.query):  # TV1 human: before cap, billing, fair use
        assert runtime is not None
        return await surfaces.answer_query(
            request, session, corpus, body, runtime, profile=profile, human_only=True
        )
    # /TV1 human
    if runtime is not None:
        # PIP3 gate: cap, billing and fair use in one decision; a pause still answers through the
        # director (the pause message and the handoff card), never with an error
        decision, resolved_llm, resolved_retrieval, profile = await gate_and_resolve(
            request,
            session,
            resolver,
            corpus,
            resolved_llm,
            resolved_retrieval,
            profile,
            headers=llm_headers,
        )
        return await surfaces.answer_query(
            request,
            session,
            corpus,
            body,
            runtime,
            **pause_args(decision),
            profile=profile,  # PIP1 profile
        )
        # /PIP3 gate

    # Check query cache first (respects per-corpus cache setting)
    query_cache = None
    if resolved_retrieval.cache_enabled:
        query_cache = _get_query_cache(request)
        cached = await query_cache.get(body.query, str(corpus.id))
        if cached is not None:
            # Monthly question cap (accounts A6). A plan that counts cached answers (the
            # default) refuses a capped tenant even a cached one and counts this answer; a
            # plan that does not serves it free, at the cap too.
            counted = await counts_cached_answers(session, corpus.tenant_id)
            # PIP3 gate: a paused workspace gets no cached answer either (a cached answer costs
            # nothing, so a downgrade changes nothing and it is not counted as a question)
            decision = await answer_gate(
                session,
                request,
                corpus,
                payer=payer_of(resolved_llm),
                surface="api",
                enforce_cap=counted,
                count_question=False,
                plan_profile=profile,
            )
            await refuse_paused(decision, request)
            # /PIP3 gate
            if counted:
                await record_usage(
                    session,
                    corpus_id=corpus.id,
                    tenant_id=corpus.tenant_id,
                    source=SOURCE_API,
                    prompt_tokens=0,
                    completion_tokens=0,
                    model=cached.get("model_used"),
                    payer=payer_of(resolved_llm),
                    request_id=uuid.uuid4(),  # BIL3 request id: every cache hit is a new answer
                )
            return QueryResponse(
                answer=cached["answer"],
                citations=cached.get("citations", []),
                confidence=cached.get("confidence", 1.0),
                query_id=cached.get("query_id"),
                model_used=cached.get("model_used", "cached"),
                latency_ms=0,
                # Served from the cache: no model call, so a known cost of nothing.
                token_usage={
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                    "cost_known": True,
                },
            )

    # A real answer (not a cache hit): the monthly question cap, billing and fair use, before
    # any model work. The legacy path has no pause reply: 402 (balance) or 429 (fair use).
    # PIP3 gate
    decision, resolved_llm, resolved_retrieval, profile = await gate_and_resolve(
        request,
        session,
        resolver,
        corpus,
        resolved_llm,
        resolved_retrieval,
        profile,
        headers=llm_headers,
    )
    await refuse_paused(decision, request)
    # /PIP3 gate

    resolved_prompt = resolver.resolve_prompt(
        request_body=({"system_prompt": body.system_prompt} if body.system_prompt else None),
        corpus_prompt_config=corpus.prompt_config,
    )

    orchestrator = _build_orchestrator(
        request,
        session,
        corpus,
        resolved_llm,
        resolved_prompt,
        resolved_retrieval=resolved_retrieval,
    )

    answer_llm, _routed = route_answer_llm(
        orchestrator.llm, resolved_llm.language_models, body.query
    )

    # PIP1 lane: retrieval and the answer hold a slot; too long a wait is a 503, never a hang
    slot = await hold_slot_or_busy(request, profile, corpus.tenant_id)
    # /PIP1 lane
    try:
        result = await orchestrator.query(
            text=body.query,
            corpus_id=corpus.id,
            **surfaces.retrieval_kwargs(body.options, resolved_retrieval, profile=profile),
            enable_validation=(
                body.options.enable_validation
                if body.options.enable_validation is not None
                else resolved_retrieval.validation_enabled
            ),
            system_prompt=resolved_prompt.system_prompt,
            constraints=resolved_prompt.constraints,
            response_language=resolved_prompt.response_language,
            answer_llm=answer_llm,
        )
        usage = result.get("token_usage") or {}
        await record_usage(
            session,
            corpus_id=corpus.id,
            tenant_id=corpus.tenant_id,
            source=SOURCE_API,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            model=result.get("model_used"),
            cost=request_cost_of(orchestrator.llm),
            payer=payer_of(resolved_llm),
            commit=False,
            request_id=result["query_id"],  # BIL3 request id
        )
        await session.commit()

        # Cache the response (if caching enabled)
        if query_cache is not None:
            await query_cache.set(body.query, str(corpus.id), result)

        return QueryResponse(
            answer=result["answer"],
            citations=result["citations"],
            confidence=result["confidence"],
            query_id=result["query_id"],
            model_used=result["model_used"],
            latency_ms=result["latency_ms"],
            token_usage=result.get("token_usage"),
        )

    except QueryError as e:
        logger.error("Query error: %s", e)
        raise HTTPException(status_code=422, detail=str(e)) from e

    except RetrievalError as e:
        logger.error("Retrieval error: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e

    except Exception as e:
        logger.error("Unexpected query error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal query error") from e

    finally:
        await slot.release()  # PIP1 lane


@router.post("/stream")
async def stream_query(
    request: Request,
    body: QueryRequest,
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Stream an answer as Server-Sent Events, in the widget's named-event format.

    Events, in order:

    - `stage` — `{"stage": "understand" | "search" | "rerank" | "write", "elapsed_ms"}`,
      repeatedly, as the engine works (`rerank` only when a re-ranker is on). `elapsed_ms`
      counts from the start of the stream, so a client can run a live clock.
    - `sources` — `{"query_id", "sources": [{index, chunk_id, document_id,
      external_id, file_name, section, page, snippet, score, score_breakdown}]}`: the
      citations the answer is grounded on, before the first token. `score_breakdown` is the
      per-signal detail `/query/retrieve` returns (null when the pipeline has none).
    - `token` — `{"content"}`, repeatedly.
    - `done` — `{"query_id", "model", "latency_ms", "usage": {prompt_tokens,
      completion_tokens, total_tokens}, "timings": {stage: ms}, "stage_timings_ms":
      {engine step: ms}, "retrieval_ms", "processed_query"}`, plus `thinking_ms` and
      `reasoning_tokens` when the model reports that it reasoned. Token counts are
      estimates: providers report no usage on a stream. `query_id` is logged before `done`
      is sent, so it works for `GET /query/{id}` and feedback straight away.
    - `error` — `{"detail", "code"?}` replaces the rest if retrieval or generation fails
      (`code` is `query_invalid` or `retrieval_failed` for a failure before the first
      source).

    With the knowledge base's assistant on (the default), the conversation director answers
    and the stream also carries `meta` (first, after the `understand` stage: label, route,
    language), `quick_replies`, `ui` and `trace` (debug only) events; `done` then adds
    `label`, `route`, `language`, `cta`, `claims_removed` and `gate_threshold`, and `usage`
    carries the real cost. `stage` also takes `check` (the confidence check). `error` codes are
    unchanged. See the README's "conversation layer" section.

    A failure before streaming starts (no such knowledge base, question cap, streaming
    switched off) is an ordinary HTTP error, as on /query. Retrieval runs inside the stream,
    so its failure is an `error` event rather than an HTTP status. Clients ignore events
    they do not know.
    """
    corpus = _resolve_corpus(request, body)
    mark_admin_answer(request)  # BIL3 request id: the operator's own tests are not billed
    if corpus is None:
        corpus = await _get_corpus_by_id(session, body.corpus_id)  # type: ignore[arg-type]

    # Tenant ownership check
    tenant = getattr(request.state, "tenant", None)
    if tenant is not None and corpus.tenant_id != tenant.id:
        raise HTTPException(status_code=403, detail="Corpus does not belong to this tenant")

    settings = request.app.state.settings
    if not settings.query_stream_enabled:
        raise HTTPException(
            status_code=403,
            detail="Streaming is disabled by configuration",
        )

    surfaces.authorize_assistant_options(request, body.assistant)
    runtime = await surfaces.assistant_for(
        session, corpus, use_draft=bool(body.assistant and body.assistant.use_draft)
    )
    resolver = _get_config_resolver(request)

    # PIP1 profile
    llm_headers = _extract_llm_headers(request)
    resolved_llm, resolved_retrieval, profile = await _resolve_for(
        request, session, resolver, corpus, headers=llm_headers or None
    )
    # /PIP1 profile
    if surfaces.is_human_turn(runtime, body.query):  # TV1 human: before cap, billing, fair use
        assert runtime is not None
        return await surfaces.stream_query_response(
            request, session, corpus, body, runtime, profile=profile, human_only=True
        )
    # /TV1 human
    # PIP3 gate: the question cap, billing and fair use are decided before the stream starts;
    # a streaming client that is refused gets the HTTP error, as before
    decision, resolved_llm, resolved_retrieval, profile = await gate_and_resolve(
        request,
        session,
        resolver,
        corpus,
        resolved_llm,
        resolved_retrieval,
        profile,
        headers=llm_headers or None,
    )
    if runtime is not None:
        return await surfaces.stream_query_response(
            request,
            session,
            corpus,
            body,
            runtime,
            **pause_args(decision),
            profile=profile,
        )
    await refuse_paused(decision, request)  # the legacy path has no pause reply
    # /PIP3 gate
    resolved_prompt = resolver.resolve_prompt(
        request_body=({"system_prompt": body.system_prompt} if body.system_prompt else None),
        corpus_prompt_config=corpus.prompt_config,
    )

    orchestrator = _build_orchestrator(
        request,
        session,
        corpus,
        resolved_llm,
        resolved_prompt,
        resolved_retrieval=resolved_retrieval,
    )

    query_id = uuid.uuid4()
    t_start = time.monotonic()
    session_factory = getattr(request.app.state, "session_factory", None)
    answer_llm, routed_model = route_answer_llm(
        orchestrator.llm, resolved_llm.language_models, body.query
    )
    model = routed_model or resolved_llm.model

    async def _answer() -> AsyncIterator[str]:
        # The clock the client sees starts now; the pipeline runs as a task so its `stage`
        # events reach the client while it works, not after it.
        clock = StageClock()
        queue: asyncio.Queue[str] = asyncio.Queue()
        first = clock.enter(STAGE_UNDERSTAND)
        if first is not None:
            yield first
        task = asyncio.ensure_future(
            orchestrator.run_retrieval_pipeline(
                text=body.query,
                corpus_id=corpus.id,
                **surfaces.retrieval_kwargs(body.options, resolved_retrieval, profile=profile),
                system_prompt=resolved_prompt.system_prompt,
                constraints=resolved_prompt.constraints,
                response_language=resolved_prompt.response_language,
                on_stage=stage_listener(clock, queue),
            )
        )
        try:
            async for event in events_until_done(task, queue):
                yield event
            pipeline = task.result()
        except QueryError as e:
            logger.error("Query error: %s", e)
            yield sse_event("error", {"detail": str(e), "code": "query_invalid"})
            return
        except RetrievalError as e:
            logger.error("Retrieval error: %s", e)
            yield sse_event("error", {"detail": str(e), "code": "retrieval_failed"})
            return
        except Exception:
            logger.exception("Streaming retrieval failed for corpus %s", corpus.id)
            yield sse_event("error", {"detail": "Internal query error", "code": "retrieval_failed"})
            return
        finally:
            # If the client left mid-search: stop the work and let it unwind (it may be mid-query
            # on this session) before the connection goes back.
            await stop_search(task)
            # Retrieval is done with the request's session: give its connection back now
            # rather than hold it for the whole stream. The stream writes through sessions
            # of its own.
            await shielded(session.close())

        messages = pipeline.assembled.messages
        cited = pipeline.assembled.cited_chunks
        sources = [
            _stream_source(cc, pipeline.score_breakdown.get(str(cc.chunk_id))) for cc in cited
        ]
        yield sse_event("sources", {"query_id": str(query_id), "sources": sources})
        retrieval_ms = clock.elapsed_ms()
        write_event = clock.enter(STAGE_WRITE)
        if write_event is not None:
            yield write_event
        write_start = time.monotonic()
        parts: list[str] = []
        try:
            with collect_stream_stats() as stream_stats:
                async for chunk in answer_llm.complete_stream(messages):
                    parts.append(chunk)
                    yield sse_token(chunk)
        except Exception:
            logger.exception("Streaming generation failed for corpus %s", corpus.id)
            # The helper calls and the partial answer were paid for: meter that spend (there is
            # no answered question to count).
            await _meter_failed_stream(
                session_factory,
                corpus,
                orchestrator.llm,
                model,
                payer_of(resolved_llm),
                uuid.uuid5(query_id, "failed"),  # BIL3 request id
            )
            yield sse_error("Generation failed part-way; the answer is incomplete.")
            return

        write_ms = round((time.monotonic() - write_start) * 1000)
        answer = "".join(parts)
        prompt_tokens = sum(estimate_tokens(m.content) for m in messages)
        completion_tokens = estimate_tokens(answer)
        usage: dict[str, Any] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        # The real cost of the whole request (answer and helper calls), from the provider's
        # own figures; `cost_known` is false when a call's price could not be found.
        spent = request_cost_of(orchestrator.llm)
        if spent is not None:
            usage.update(spent.as_dict())
        latency_ms = int((time.monotonic() - t_start) * 1000)
        # Logged and metered before `done`, so the query_id a client gets back
        # already resolves and a client that hangs up on `done` cannot cut a
        # write off half-way (shielded as well). Own sessions: the request's
        # was released before the stream started.
        await shielded(
            _log_streamed_query(
                session_factory,
                query_id=query_id,
                corpus_id=corpus.id,
                query_text=pipeline.processed_query,
                chunk_ids=[cc.chunk_id for cc in cited],
                response=answer,
                latency_ms=latency_ms,
                model=model,
                token_usage=usage,
            )
        )
        await shielded(
            record_usage_new_session(
                session_factory,
                corpus_id=corpus.id,
                tenant_id=corpus.tenant_id,
                source=SOURCE_STREAM,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                model=model,
                cost=spent,
                payer=payer_of(resolved_llm),
                request_id=query_id,  # BIL3 request id
            )
        )
        timings = public_timings(pipeline.stage_timings_ms)
        timings[STAGE_WRITE] = write_ms
        done: dict[str, Any] = {
            "query_id": str(query_id),
            "model": model,
            "latency_ms": latency_ms,
            "usage": usage,
            # Per stage (understand / search / rerank / write), plus the engine's finer
            # steps and the time to the first word. Additive: older clients ignore them.
            "timings": timings,
            "stage_timings_ms": pipeline.stage_timings_ms,
            "retrieval_ms": retrieval_ms,
            "processed_query": pipeline.processed_query,
        }
        thinking_ms = stream_stats.thinking_ms
        if thinking_ms is not None:
            done["thinking_ms"] = thinking_ms
        if stream_stats.reasoning_tokens > 0:
            done["reasoning_tokens"] = stream_stats.reasoning_tokens
            done["usage"] = {**usage, "reasoning_tokens": stream_stats.reasoning_tokens}
        yield sse_event("done", done)

    return StreamingResponse(
        slotted_stream(request, profile, corpus.tenant_id, _answer()),  # PIP1 lane
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _stream_source(cc: Any, breakdown: Any = None) -> dict[str, Any]:
    """One citation for the `sources` event: the widget's fields plus /query's ids, and the
    per-signal score breakdown `/query/retrieve` returns (None when the pipeline has none),
    so a client needs no second retrieval to explain the ranking. One formatter, shared with
    the assistant's streams (`conversation.envelope`)."""
    from callobuzz_rag.conversation.envelope import source_of, stream_source

    return stream_source(source_of(cc, breakdown))


async def _log_streamed_query(
    session_factory: Any,
    *,
    query_id: UUID,
    corpus_id: UUID,
    query_text: str,
    chunk_ids: list[Any],
    response: str,
    latency_ms: int,
    model: str,
    token_usage: dict[str, Any],
) -> None:
    """Write the query_log row for a streamed answer. Best-effort, like metering.

    confidence_score stays NULL: a stream is never validated, and 1.0 would
    claim a check that did not happen.
    """
    if session_factory is None:
        return
    try:
        async with session_factory() as session:
            session.add(
                QueryLog(
                    id=query_id,
                    corpus_id=corpus_id,
                    query_text=query_text,
                    retrieved_chunk_ids=[
                        c if isinstance(c, uuid.UUID) else uuid.UUID(str(c)) for c in chunk_ids
                    ],
                    response=response,
                    confidence_score=None,
                    latency_ms=latency_ms,
                    model_used=model[:100],
                    token_usage=token_usage,
                )
            )
            await session.commit()
    except Exception:
        logger.warning("Could not log streamed query %s", query_id, exc_info=True)


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve_chunks(
    request: Request,
    body: RetrieveRequest,
    session: AsyncSession = Depends(get_session),
) -> RetrieveResponse:
    """Retrieval-only endpoint — returns ranked chunks without LLM generation.

    Use this when the caller has its own LLM and only needs relevant document
    chunks as context. Runs steps 1-13 of the query pipeline (embedding,
    vector search, reranking, context assembly) but skips LLM generation,
    validation, and query logging.
    """
    import time

    corpus = _resolve_corpus(request, body)
    if corpus is None:
        corpus = await _get_corpus_by_id(session, body.corpus_id)  # type: ignore[arg-type]

    # Tenant ownership check
    tenant = getattr(request.state, "tenant", None)
    if tenant is not None and corpus.tenant_id != tenant.id:
        raise HTTPException(status_code=403, detail="Corpus does not belong to this tenant")

    resolver = _get_config_resolver(request)

    # LLM config still needed for HyDE (if enabled), but not for generation; the profile's
    # retrieval settings apply (PIP1 profile). Search-only is never billing-gated, but it
    # runs the CPU-heavy part (rerank), so it holds an answer slot like an answer does.
    llm_headers = _extract_llm_headers(request)
    resolved_llm, resolved_retrieval, profile = await _resolve_for(
        request, session, resolver, corpus, headers=llm_headers or None
    )
    resolved_prompt = resolver.resolve_prompt(
        request_body=({"system_prompt": body.system_prompt} if body.system_prompt else None),
        corpus_prompt_config=corpus.prompt_config,
    )

    orchestrator = _build_orchestrator(
        request,
        session,
        corpus,
        resolved_llm,
        resolved_prompt,
        resolved_retrieval=resolved_retrieval,
    )

    t_start = time.monotonic()

    slot = await hold_slot_or_busy(request, profile, corpus.tenant_id)  # PIP1 lane
    limited = surfaces.clamp_options(body.options, resolved_retrieval, profile) or body.options
    try:
        pipeline = await orchestrator.run_retrieval_pipeline(
            text=body.query,
            corpus_id=corpus.id,
            top_k=limited.top_k or resolved_retrieval.top_k,
            final_k=limited.final_k or resolved_retrieval.final_k,
            enable_hyde=(
                limited.enable_hyde
                if limited.enable_hyde is not None
                else resolved_retrieval.hyde_enabled
            ),
            enable_classification=(
                limited.enable_classification
                if limited.enable_classification is not None
                else resolved_retrieval.query_classification_enabled
            ),
            enable_mmr=resolved_retrieval.mmr_enabled,
            enable_reranking=resolved_retrieval.reranking_enabled,
            enable_temporal_decay=resolved_retrieval.temporal_decay_enabled,
            enable_hybrid=(
                body.options.search_mode == "hybrid"
                if body.options.search_mode is not None
                else resolved_retrieval.hybrid_search_enabled
            ),
            keyword_only=body.options.search_mode == "keyword",
            enable_pipeline_order=resolved_retrieval.pipeline_order_enabled,
            system_prompt=resolved_prompt.system_prompt,
            constraints=resolved_prompt.constraints,
            response_language=resolved_prompt.response_language,
            category=body.options.category,
            category_weights=body.options.category_weights,
            user_id=body.options.user_id,
            metadata_filters=body.options.metadata_filters,
            # The context budget sizes an LLM prompt this endpoint never builds.
            context_budget=False,
        )

        latency_ms = int((time.monotonic() - t_start) * 1000)

        chunks = [
            RetrievedChunk(
                chunk_id=str(cc.chunk_id),
                text=cc.text,
                file_name=cc.file_name,
                document_id=str(cc.document_id) if cc.document_id else None,
                external_id=cc.external_id,
                section=cc.section,
                page=cc.page,
                score=cc.score,
                token_count=cc.token_count,
                also_in=pipeline.also_in.get(str(cc.chunk_id), []),
                score_breakdown=(
                    ScoreBreakdown(**asdict(breakdown))
                    if (breakdown := pipeline.score_breakdown.get(str(cc.chunk_id)))
                    else None
                ),
            )
            for cc in pipeline.assembled.cited_chunks
        ]

        return RetrieveResponse(
            chunks=chunks,
            processed_query=pipeline.processed_query,
            latency_ms=latency_ms,
            total_chunks=len(chunks),
            stage_timings_ms=pipeline.stage_timings_ms,
        )

    except RetrievalError as e:
        logger.error("Retrieval error: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e

    except Exception as e:
        logger.error("Unexpected retrieval error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal retrieval error") from e
    finally:
        await slot.release()  # PIP1 lane


@router.get("/{query_id}", response_model=QueryHistoryResponse)
async def get_query(
    query_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> QueryLog:
    """Retrieve a past query and its response by ID.

    Scoped like every other read: a corpus key sees its own queries, a tenant key
    its corpora's, admin everything. A query outside the caller's scope is a 404,
    not a 403, so ids cannot be probed for existence.
    """
    allowed = await authorized_corpus_ids(resolve_scope(request), None, session)
    log = await session.get(QueryLog, query_id)
    if not log or (allowed is not None and log.corpus_id not in allowed):
        raise HTTPException(status_code=404, detail="Query not found")
    return log
