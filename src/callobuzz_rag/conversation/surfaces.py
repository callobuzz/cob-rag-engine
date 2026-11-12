"""Wire the director into the answering routes. Routes call these through the module
(`surfaces.assistant_for(...)`) so tests can switch the assistant off at one point.

Import-light on purpose: every route test imports this module (the autouse `_assistant_off`
fixture patches it), so the director, the answer step and the providers are imported inside
the functions that use them.
"""

import contextlib
import re
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import Request
from fastapi.responses import StreamingResponse

from callobuzz_rag.analytics.usage import SOURCE_API, SOURCE_STREAM
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.conversations import MessageResponse, MessageSendResponse
from callobuzz_rag.conversation.sessions import widget_conversation
from callobuzz_rag.conversation.state import ConversationState, load_state
from callobuzz_rag.conversation.store import RuntimeConfig, get_runtime_config
from callobuzz_rag.conversation.types import Channel
from callobuzz_rag.core.pause import PausedBy
from callobuzz_rag.tiers import profiles as tier_profiles  # PIP1 profile
from callobuzz_rag.tiers.apply import runtime_with_profile  # PIP1 profile
from callobuzz_rag.tiers.resolve import resolve_with_profile  # PIP1 profile
from callobuzz_rag.tiers.slot import slot_spec, worker_slot  # PIP1 lane

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.api.schemas.assistant import AssistantRequestOptions
    from callobuzz_rag.api.schemas.query import QueryOptions, QueryRequest, QueryResponse
    from callobuzz_rag.conversation.cache import RedisJsonCache
    from callobuzz_rag.conversation.director import Director, TurnInput
    from callobuzz_rag.conversation.envelope import DoneEvent, Surface
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.core.config_resolver import ResolvedRetrievalConfig
    from callobuzz_rag.db.models.conversation import Conversation
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.llm.models import LLMMessage
    from callobuzz_rag.tiers.schema import EffectiveProfile


async def assistant_for(
    session: "AsyncSession", corpus: "Corpus", *, use_draft: bool
) -> RuntimeConfig | None:
    """The runtime config the director answers with, or None when the knowledge base's
    assistant is switched off (every surface then takes today's code path)."""
    runtime = await get_runtime_config(session, corpus.id, use_draft=use_draft)
    if runtime is None:
        return None
    if use_draft:
        return runtime  # previewing works on a disabled draft too
    return runtime if runtime.config.enabled else None


def is_human_turn(runtime: RuntimeConfig | None, text: str) -> bool:  # TV1 human
    """True when the assistant is on and the visitor asks for a person. Such a turn skips the
    question cap, the billing pause, fair use and the answer lane (it costs nothing and is never
    counted), and the director answers it with `human_only` (rules label, no search, no model)."""
    from callobuzz_rag.conversation.human import is_human_request

    return runtime is not None and runtime.config.enabled and is_human_request(text)


def authorize_assistant_options(request: Request, opts: "AssistantRequestOptions | None") -> None:
    """`use_draft`, `debug` and `simulate` are request-body fields on a `read` route: they
    pull the draft, the compiled prompt and chunk scores, so they need more than reading.

    - an API key needs `write` permission (a `read` key may only ask);
    - `debug` needs an owner (`owner_required` for a member's sign-in);
    - `simulate` only makes sense with `use_draft` or `debug`.
    """
    if opts is None:
        return
    privileged = opts.use_draft or opts.debug or opts.simulate is not None
    if privileged:
        from callobuzz_rag.api.permissions import permission_allows

        info = getattr(request.state, "api_key", None)
        if info is not None and not permission_allows(info.permission, "write"):
            raise APIError(
                403,
                f"This API key has {info.permission} permission; this needs write",
                "key_permission_denied",
                permission=info.permission,
                required="write",
            )
    if opts.debug:
        from callobuzz_rag.api.deps import require_owner

        require_owner(request)
    if opts.simulate is not None and not (opts.use_draft or opts.debug):
        raise APIError(
            422,
            "assistant.simulate needs assistant.use_draft or assistant.debug",
            "invalid_assistant_options",
        )


def clamp_options(
    options: "QueryOptions | None",
    resolved: "ResolvedRetrievalConfig",
    profile: "EffectiveProfile | None",
) -> "QueryOptions | None":
    """A request's `options` under the business's pipeline profile (PIP-8). A profile that
    replaces the knowledge base's settings (Free, every downgrade) caps what a caller may ask
    for: the options may narrow what the profile resolved (`resolved` already carries it), never
    widen it: no wider search, no more passages, no query rewriting or HyDE the profile turned
    off. Any other profile leaves the options as they are."""
    if options is None or profile is None or not profile.config.overrides_kb_settings:
        return options
    update: dict[str, Any] = {}
    if options.top_k is not None:
        update["top_k"] = min(options.top_k, resolved.top_k)
    if options.final_k is not None:
        update["final_k"] = min(options.final_k, resolved.final_k)
    if options.enable_hyde:
        update["enable_hyde"] = resolved.hyde_enabled
    if options.enable_classification:
        update["enable_classification"] = resolved.query_classification_enabled
    return options.model_copy(update=update) if update else options


def retrieval_kwargs(
    options: "QueryOptions | None",
    resolved: "ResolvedRetrievalConfig",
    *,
    profile: "EffectiveProfile | None" = None,
) -> dict[str, Any]:
    """The option -> retrieval pipeline mapping every answering route shares: the request's
    `options` (within the profile's limits, `clamp_options`) over the knowledge base's resolved
    retrieval config. The prompt (the director compiles it), validation (the fact check
    replaces it) and `on_stage` are the caller's."""
    o = clamp_options(options, resolved, profile)
    search_mode = o.search_mode if o is not None else None
    return {
        "top_k": (o.top_k if o and o.top_k else resolved.top_k),
        "final_k": (o.final_k if o and o.final_k else resolved.final_k),
        "enable_hyde": (
            o.enable_hyde if o and o.enable_hyde is not None else resolved.hyde_enabled
        ),
        "enable_classification": (
            o.enable_classification
            if o and o.enable_classification is not None
            else resolved.query_classification_enabled
        ),
        "enable_mmr": resolved.mmr_enabled,
        "enable_reranking": resolved.reranking_enabled,
        "enable_temporal_decay": resolved.temporal_decay_enabled,
        "enable_hybrid": (
            search_mode == "hybrid" if search_mode is not None else resolved.hybrid_search_enabled
        ),
        "keyword_only": search_mode == "keyword",
        "enable_pipeline_order": resolved.pipeline_order_enabled,
        "category": o.category if o else None,
        "category_weights": o.category_weights if o else None,
        "user_id": o.user_id if o else None,
        "metadata_filters": o.metadata_filters if o else None,
    }


def _new_caches(settings: "Settings") -> "tuple[RedisJsonCache | None, RedisJsonCache | None]":
    """(label cache, translation cache). The label key already carries `alabel:v1:`
    (`label_cache_key`), so its prefix is empty; C2's translation key is a bare sha256, so
    that cache gets `atrans:v1:` (README P5)."""
    try:
        from callobuzz_rag.conversation.cache import RedisJsonCache

        return (
            RedisJsonCache(settings.redis_url),
            RedisJsonCache(settings.redis_url, prefix="atrans:v1:"),
        )
    except Exception:
        return None, None


def _caches(request: Request) -> "tuple[RedisJsonCache | None, RedisJsonCache | None]":
    caches = getattr(request.app.state, "assistant_caches", None)
    if caches is None:
        caches = _new_caches(request.app.state.settings)
        request.app.state.assistant_caches = caches
    return caches


def build_director(
    request: Request,
    corpus: "Corpus",
    *,
    use_headers: bool,
    options: "QueryOptions | None",
    llm_config_override: dict[str, Any] | None = None,
    retrieval_filters: dict[str, Any] | None = None,
    profile: "EffectiveProfile | None" = None,  # PIP1 profile
) -> "Director":
    """A director for one request. ONE LLM provider is built and shared by the labeller, the
    translator, the retrieval orchestrator and the answer step, so its cost ledger is the
    whole turn (billing reads it).

    COB (the built-in assistant) passes two keyword-only extras: `llm_config_override` replaces
    the knowledge base's own `llm_config` for this turn (a workspace's own key answers its own
    COB questions), and `retrieval_filters` are metadata filters merged over whatever the
    options asked for (the audience filter, which no request field may widen)."""
    from callobuzz_rag.analytics.usage import payer_of
    from callobuzz_rag.api.routes.query import (
        _build_orchestrator,
        _extract_llm_headers,
        _get_config_resolver,
    )
    from callobuzz_rag.conversation.answering import AnswerDeps
    from callobuzz_rag.conversation.director import Director
    from callobuzz_rag.conversation.labeler import Labeler
    from callobuzz_rag.llm.litellm_provider import LiteLLMProvider

    settings = request.app.state.settings
    resolver = _get_config_resolver(request)
    headers = _extract_llm_headers(request) if use_headers else {}
    # PIP1 profile: the business's pipeline profile over the knowledge base's settings; no
    # profile (COB answering with its own configuration) is the no-op Standard.
    resolved_llm, resolved_retrieval = resolve_with_profile(
        resolver,
        corpus,
        headers=headers or None,
        profile=profile or tier_profiles.noop_profile(),
        settings=settings,
        llm_config_override=llm_config_override,
    )
    # /PIP1 profile
    resolved_prompt = resolver.resolve_prompt(
        request_body=None, corpus_prompt_config=corpus.prompt_config
    )
    llm = LiteLLMProvider.from_resolved_config(resolved_llm)
    label_cache, translate_cache = _caches(request)
    turn_retrieval = retrieval_kwargs(options, resolved_retrieval, profile=profile)
    if retrieval_filters:
        turn_retrieval["metadata_filters"] = {
            **(turn_retrieval.get("metadata_filters") or {}),
            **retrieval_filters,
        }

    def factory(session: "AsyncSession") -> Any:
        return _build_orchestrator(
            request,
            session,
            corpus,
            resolved_llm,
            resolved_prompt,
            resolved_retrieval=resolved_retrieval,
            llm=llm,
        )

    deps = AnswerDeps(
        settings=settings,
        session_factory=request.app.state.session_factory,
        orchestrator_factory=factory,
        retrieval_kwargs=turn_retrieval,
        llm=llm,
        model_name=resolved_llm.model,
        translate_cache=translate_cache,
        payer=payer_of(resolved_llm),
        language_models=dict(resolved_llm.language_models),
    )
    labeler = Labeler(
        llm=llm,
        cache=label_cache,
        timeout_s=settings.assistant_label_timeout_ms / 1000,
        ttl_s=settings.assistant_label_cache_ttl_s,
    )
    return Director(deps, labeler=labeler)


def director_for_worker(
    session_factory: "Callable[[], AsyncSession]",
    settings: "Settings",
    corpus: "Corpus",
    *,
    use_draft: bool,
    retrieval_overrides: dict[str, Any] | None = None,
    llm_override: dict[str, str] | None = None,
    profile: "EffectiveProfile | None" = None,  # PIP1 profile
    patient: bool = True,  # PIP1 lane
) -> "Director":
    """A director for code with no request (C7's golden runs in the worker; README P15).

    Same providers as the app's lifespan (`api/app.py`), the same corpus-level resolution as
    `build_director`, never X-LLM-* headers. `retrieval_overrides` are `RetrievalConfigInput`
    fields applied over the corpus's own retrieval config (an experiment's search settings);
    `llm_override` is `{"provider", "model", "api_key", "base_url"}` (any subset) applied
    over the corpus's LLM config (an experiment's answer model), passed exactly like the
    X-LLM-* headers would be. `use_draft` is accepted for symmetry: which config the turn
    uses is the `RuntimeConfig` the caller puts on `TurnInput`; the director is config-agnostic.

    A worker task runs in its own `asyncio.run()`, so the embedder is built uncached (a cached
    one is bound to another loop) and the director owns it and the vector client: call
    `await director.aclose()` when the run ends.
    """
    from callobuzz_rag.analytics.usage import payer_of
    from callobuzz_rag.conversation.answering import AnswerDeps
    from callobuzz_rag.conversation.director import Director
    from callobuzz_rag.conversation.labeler import Labeler
    from callobuzz_rag.core.config_resolver import ConfigResolver
    from callobuzz_rag.core.secrets import encryptor_from_settings
    from callobuzz_rag.embeddings.factory import create_embedding_provider, embedder_for_corpus
    from callobuzz_rag.llm.litellm_provider import LiteLLMProvider
    from callobuzz_rag.retrieval.context_assembly import ContextAssembler
    from callobuzz_rag.retrieval.orchestrator import QueryOrchestrator
    from callobuzz_rag.retrieval.safety import ContentSanitizer
    from callobuzz_rag.vectordb.qdrant import QdrantVectorProvider

    del use_draft  # documented above
    resolver = ConfigResolver(settings, encryptor=encryptor_from_settings(settings))
    retrieval_config = {**(corpus.retrieval_config or {}), **(retrieval_overrides or {})}
    headers = {f"x-llm-{k.replace('_', '-')}": v for k, v in (llm_override or {}).items() if v}
    # PIP1 profile: golden runs answer with the plan's profile, never a downgrade (D10)
    resolved_llm, resolved_retrieval = resolve_with_profile(
        resolver,
        corpus,
        headers=headers or None,
        profile=profile or tier_profiles.noop_profile(),
        settings=settings,
        retrieval_config_override=retrieval_config,
    )
    # /PIP1 profile
    llm = LiteLLMProvider.from_resolved_config(resolved_llm)
    default_embedder = create_embedding_provider(settings, cache=False)
    embedder = embedder_for_corpus(corpus, settings, default_embedder, cache=False)
    vector_store = QdrantVectorProvider(settings)
    assembler = ContextAssembler(settings, sanitizer=ContentSanitizer(settings))
    run_settings = settings.model_copy(
        update={
            "fusion_strategy": resolved_retrieval.fusion_strategy,
            "hybrid_dense_weight": resolved_retrieval.hybrid_dense_weight,
            "rrf_k": resolved_retrieval.rrf_k,
            "mmr_lambda": resolved_retrieval.mmr_lambda,
            "collapse_duplicates": resolved_retrieval.collapse_duplicates,
        }
    )

    def factory(session: "AsyncSession") -> Any:
        # No cross-encoder in the worker: a golden run measures the corpus's answer path with
        # its configured reranker only when that is the LLM reranker.
        from callobuzz_rag.api.routes.query import _select_reranker

        return QueryOrchestrator(
            session=session,
            settings=run_settings,
            embedder=embedder,
            vector_store=vector_store,
            llm_provider=llm,
            context_assembler=assembler,
            reranker=_select_reranker(None, llm, resolved_retrieval),
            collection_name=corpus.qdrant_collection,
        )

    async def close() -> None:
        for resource in {id(embedder): embedder, id(default_embedder): default_embedder}.values():
            await resource.close()
        await vector_store.close()

    label_cache, translate_cache = _new_caches(settings)
    deps = AnswerDeps(
        settings=settings,
        session_factory=session_factory,
        orchestrator_factory=factory,
        retrieval_kwargs=retrieval_kwargs(None, resolved_retrieval),
        llm=llm,
        model_name=resolved_llm.model,
        translate_cache=translate_cache,
        payer=payer_of(resolved_llm),
        language_models=dict(resolved_llm.language_models),
    )
    labeler = Labeler(
        llm=llm,
        cache=label_cache,
        timeout_s=settings.assistant_label_timeout_ms / 1000,
        ttl_s=settings.assistant_label_cache_ttl_s,
    )
    # PIP1 lane: a golden run waits for an answer slot far longer than a visitor would; a
    # visitor's chat (`patient=False`, WhatsApp) waits like the widget's and gets the busy reply
    slot, close_lanes = worker_slot(
        settings, profile or tier_profiles.noop_profile(), patient=patient
    )
    director = Director(
        deps, labeler=labeler, closers=[close] + ([close_lanes] if close_lanes else [])
    )
    director.default_slot = slot
    return director
    # /PIP1 lane


async def conversation_context(
    session: "AsyncSession",
    corpus_id: uuid.UUID,
    conversation_id: uuid.UUID,
    settings: "Settings",
    kind: str = "assistant",
    staff_label: str | None = None,  # TV6 channel
) -> "tuple[Conversation, ConversationState, list[LLMMessage]]":
    """A stored conversation of this knowledge base, its director state and its history.

    The history is what conversation messages have always sent the model: the earlier
    summary when the chat is long, then the recent messages within the token budget
    (`RAG_MAX_CONVERSATION_HISTORY`, `RAG_CONVERSATION_CONTEXT_BUDGET`).
    """
    from callobuzz_rag.db.models.conversation import Conversation
    from callobuzz_rag.llm.models import LLMMessage
    from callobuzz_rag.memory.context_builder import ConversationContextBuilder

    conv = await session.get(Conversation, conversation_id)
    # `kind` keeps the two worlds apart: a knowledge base's chats are 'assistant', a
    # person's chats with COB are 'cob' and reachable only through COB's own routes.
    if (
        conv is None
        or conv.corpus_id != corpus_id
        or (getattr(conv, "kind", None) or "assistant") != kind
    ):
        raise APIError(404, "Conversation not found", "conversation_not_found")
    builder = ConversationContextBuilder(
        session,
        max_messages=settings.max_conversation_history,
        token_budget=settings.conversation_context_budget,
    )
    raw = await builder.build_context(conversation_id)
    history = [
        LLMMessage(role=m["role"], content=m["content"])
        for m in raw
        if m["role"] in ("user", "assistant", "system")
    ]
    # TV6 channel: what the owner wrote himself (a `staff` message) is part of the chat the
    # assistant continues, as an assistant turn labelled with the team's name, so it neither
    # contradicts nor repeats it.
    if staff_label:
        history = [
            LLMMessage(
                role="assistant" if m["role"] == "staff" else m["role"],
                content=f"[{staff_label}] {m['content']}" if m["role"] == "staff" else m["content"],
            )
            for m in raw
            if m["role"] in ("user", "assistant", "system", "staff")
        ]
    # /TV6 channel
    return conv, load_state(conv), history


def make_turn(
    *,
    corpus: "Corpus",
    runtime: RuntimeConfig,
    text: str,
    channel: Channel,
    usage_source: str,
    conversation_id: uuid.UUID | None,
    state: ConversationState,
    history: "list[LLMMessage]",
    page_url: str | None,
    session_token: str | None,
    opts: "AssistantRequestOptions | None",
    one_off: str | None,
    widget_key_id: uuid.UUID | None = None,
    utm: dict[str, str] | None = None,
    billing_paused: bool = False,  # BIL4 paused
    paused_by: PausedBy | None = None,  # PIP3 gate
    learning: bool = False,  # BIZ4 learning
    slot: Any = None,  # PIP1 lane: the answer slot this turn holds (tiers.slot.SlotSpec)
    human_only: bool = False,  # TV1 human
    request_id: uuid.UUID | None = None,  # TV6 channel
    contact_phone: str | None = None,  # TV6 channel
) -> "TurnInput":
    from callobuzz_rag.conversation.director import TurnInput

    preview = bool(opts and (opts.use_draft or opts.debug))
    return TurnInput(
        corpus_id=corpus.id,
        tenant_id=corpus.tenant_id,
        applied_embedding=getattr(corpus, "applied_embedding", None),
        runtime=runtime,
        text=text,
        channel=Channel.PLAYGROUND if preview else channel,
        usage_source=usage_source,
        conversation_id=conversation_id,
        state=state,
        history=history,
        page_url=(opts.page_url if opts and opts.page_url else page_url),
        session_token=session_token,
        debug=bool(opts and opts.debug),
        is_preview=preview,
        simulate=opts.simulate if opts else None,
        one_off_instructions=one_off,
        now=datetime.now(UTC),
        widget_key_id=widget_key_id,
        utm=utm,
        billing_paused=billing_paused,  # BIL4 paused
        paused_by=paused_by,  # PIP3 gate
        learning=learning,  # BIZ4 learning
        slot=slot,  # PIP1 lane
        human_only=human_only,  # TV1 human
        request_id=request_id,  # TV6 channel
        contact_phone=contact_phone,  # TV6 channel
    )


async def sse_stream(
    director: "Director", turn: "TurnInput", surface: "Surface", *, show_sources: bool = True
) -> AsyncIterator[str]:
    from callobuzz_rag.conversation.envelope import to_sse

    try:
        async with contextlib.aclosing(director.run_stream(turn)) as events:
            async for event in events:
                frame = to_sse(event, surface, show_sources=show_sources)
                if frame is not None:
                    yield frame
    finally:
        await director.aclose()


def reply_fields(done: "DoneEvent") -> dict[str, Any]:
    return {
        "label": done.label,
        "route": done.route,
        "language": done.language,
        "quick_replies": done.quick_replies,
        "cta": done.cta,
        "ui": done.ui,
        "timings": done.timings,
        "conversation_id": done.conversation_id,
        "assistant_version": done.assistant_version,
        "trace": done.trace,
        "paused": done.paused,  # BIL4 paused
        "pause_reason": done.pause_reason,  # PIP3 gate
    }


async def _turn_for_query(
    request: Request,
    session: "AsyncSession",
    corpus: "Corpus",
    body: "QueryRequest",
    runtime: RuntimeConfig,
    source: str,
    channel: Channel,
    billing_paused: bool = False,
    paused_by: PausedBy | None = None,  # PIP3 gate
    profile: "EffectiveProfile | None" = None,  # PIP1 profile
    human_only: bool = False,  # TV1 human
) -> "tuple[Director, TurnInput]":
    opts = body.assistant
    state = ConversationState()
    history: list[LLMMessage] = []
    conv_id: uuid.UUID | None = None
    if opts is not None and opts.conversation_id is not None:
        conv, state, history = await conversation_context(
            session, corpus.id, opts.conversation_id, request.app.state.settings
        )
        conv_id = conv.id
    # PIP1 profile
    profile = profile or await tier_profiles.plan_profile(
        session, corpus.tenant_id, settings=request.app.state.settings
    )
    director = build_director(
        request, corpus, use_headers=True, options=body.options, profile=profile
    )
    runtime = runtime_with_profile(runtime, profile)
    # /PIP1 profile
    from callobuzz_rag.conversation import learning as site_learning  # BIZ4 learning

    turn = make_turn(
        corpus=corpus,
        runtime=runtime,
        text=body.query,
        channel=channel,
        usage_source=source,
        conversation_id=conv_id,
        state=state,
        history=history,
        page_url=None,
        session_token=None,
        opts=opts,
        one_off=body.system_prompt,
        billing_paused=billing_paused,  # BIL4 paused
        paused_by=paused_by,  # PIP3 gate
        learning=await site_learning.kb_is_learning(session, corpus.id),  # BIZ4 learning
        slot=slot_spec(request.app, profile),  # PIP1 lane
        human_only=human_only,  # TV1 human
    )
    return director, turn


def _api_error(exc: Exception) -> APIError:
    """The HTTP error for a turn that ended in an `error` event: 422 for an unusable question,
    502 for a failed search or answer (as the old path answered)."""
    detail = str(getattr(exc, "detail", exc))
    code = getattr(exc, "code", None)
    if code == "query_invalid":
        return APIError(422, detail, "query_invalid")
    if code in ("demo_limit", "demo_unavailable"):  # L5a demo: the polite text, never a 502
        return APIError(429 if code == "demo_limit" else 503, detail, code)
    return APIError(502, detail, code or "assistant_failed")


async def answer_query(
    request: Request,
    session: "AsyncSession",
    corpus: "Corpus",
    body: "QueryRequest",
    runtime: RuntimeConfig,
    *,
    billing_paused: bool = False,
    paused_by: PausedBy | None = None,  # PIP3 gate
    profile: "EffectiveProfile | None" = None,  # PIP1 profile
    human_only: bool = False,  # TV1 human
) -> "QueryResponse":
    from callobuzz_rag.api.schemas.query import CitationSource, QueryResponse
    from callobuzz_rag.conversation.director import DirectorError

    director, turn = await _turn_for_query(
        request,
        session,
        corpus,
        body,
        runtime,
        SOURCE_API,
        Channel.API,
        billing_paused,
        paused_by,
        profile,
        human_only,  # TV1 human
    )
    try:
        done = await director.run(turn)
    except DirectorError as exc:
        raise _api_error(exc) from exc
    finally:
        await director.aclose()
    citations = [
        CitationSource(
            chunk_id=s["chunk_id"],
            document_id=s["document_id"],
            external_id=s["external_id"],
            section=s["section"],
            file_name=s["file_name"],
            text_snippet=s["text"][:200] + ("..." if len(s["text"]) > 200 else ""),
            page=s["page"],
            score=s["score"],
        )
        for s in done.sources
    ]
    return QueryResponse(
        answer=done.text,
        citations=citations,
        confidence=1.0,
        query_id=done.query_id,
        model_used=done.model or "none",
        latency_ms=done.latency_ms,
        token_usage=done.usage,
        **reply_fields(done),
    )


async def stream_query_response(
    request: Request,
    session: "AsyncSession",
    corpus: "Corpus",
    body: "QueryRequest",
    runtime: RuntimeConfig,
    *,
    billing_paused: bool = False,
    paused_by: PausedBy | None = None,  # PIP3 gate
    profile: "EffectiveProfile | None" = None,  # PIP1 profile
    human_only: bool = False,  # TV1 human
) -> StreamingResponse:
    from callobuzz_rag.conversation.envelope import Surface

    director, turn = await _turn_for_query(
        request,
        session,
        corpus,
        body,
        runtime,
        SOURCE_STREAM,
        Channel.STREAM,
        billing_paused,
        paused_by,
        profile,
        human_only,  # TV1 human
    )
    await session.close()  # the stream writes through sessions of its own
    return StreamingResponse(
        sse_stream(director, turn, Surface.STREAM),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def widget_assistant_fields(runtime: RuntimeConfig | None) -> dict[str, str | None]:
    """What `GET /widget/config` adds when the assistant is on: its name, avatar, the AI
    disclosure line (when switched on) and its primary language."""
    from callobuzz_rag.conversation.schema import fill_persona, localized

    if runtime is None:
        return {"assistant_name": None, "avatar_url": None, "ai_disclosure": None, "language": None}
    cfg = runtime.config
    disclosure = (
        fill_persona(localized(cfg.identity.ai_disclosure_text, cfg.language.primary), cfg)
        if cfg.identity.ai_disclosure
        else None
    )
    return {
        "assistant_name": cfg.identity.assistant_name,
        "avatar_url": cfg.identity.avatar_url,
        "ai_disclosure": disclosure or None,
        "language": cfg.language.primary,
    }


def widget_human_button(runtime: RuntimeConfig | None, *, allow_hide: bool) -> bool:  # TV1 human
    """Whether the widget header shows "Talk to a person": the assistant is on, there is a way to
    reach someone (never a dead button), and the owner has not hidden it where the deployer lets
    them (`trust.allow_hide_human_button`)."""
    from callobuzz_rag.conversation.handoff.provider import has_human_path

    if runtime is None:
        return False
    config = runtime.config
    if not has_human_path(config):
        return False
    return config.handoff.show_human_button or not allow_hide


# TV5 disclosure
AI_BADGE: dict[str, str] = {"en": "AI assistant", "hi": "एआई सहायक", "hi-Latn": "AI assistant"}
_AI_WORD = re.compile(r"(?<!\w)(?:AI|एआई)(?!\w)", re.IGNORECASE)


def disclosure_line(config: Any, lang: str, *, editable: bool) -> str:
    """The first line the widget shows: the owner's disclosure text when the deployer lets them
    word it and it still says "AI" (a whole word), else the default text. An edit can reword the
    disclosure, never remove it."""
    from callobuzz_rag.conversation.schema import _DISCLOSURE, fill_persona, localized

    if editable:
        owner = fill_persona(localized(config.identity.ai_disclosure_text, lang), config)
        if _AI_WORD.search(owner):
            return owner
    return fill_persona(localized(_DISCLOSURE, lang), config)


# The first line when no assistant is configured (it is switched off): the owner's wording does
# not exist then, so the line is fixed. Switching the assistant off never removes the disclosure.
AI_LINE: dict[str, str] = {
    "en": "You're chatting with an AI assistant.",
    "hi": "आप एक एआई सहायक से बात कर रहे हैं।",
    "hi-Latn": "Aap ek AI assistant se baat kar rahe hain.",
}


def widget_disclosure(
    runtime: RuntimeConfig | None, *, required: bool = True, editable: bool = True
) -> dict[str, Any]:
    """The badge, the first line (also per supported language, for a visitor whose language is
    not the primary one) and whether the deployer locks them, for `GET /widget/config`. Shown
    when the deployer requires it or the owner has it on."""
    if runtime is None:
        return {
            "ai_badge": AI_BADGE["en"] if required else None,
            "disclosure_line": AI_LINE["en"] if required else None,
            "disclosure_lines": dict(AI_LINE) if required else {},
            "disclosure_required": required,
        }
    config = runtime.config
    shown = required or config.identity.ai_disclosure
    lang = config.language.primary
    languages = list(dict.fromkeys([lang, *config.language.supported]))
    return {
        "ai_badge": AI_BADGE.get(lang, AI_BADGE["en"]) if shown else None,
        "disclosure_line": disclosure_line(config, lang, editable=editable) if shown else None,
        "disclosure_lines": (
            {k: disclosure_line(config, k, editable=editable) for k in languages} if shown else {}
        ),
        "disclosure_required": required,
    }


# /TV5 disclosure


def widget_proactive(runtime: RuntimeConfig | None) -> list[Any]:
    """The greetings `GET /widget/config` lists (conversation C9): the enabled ones, their
    message in the assistant's primary language. Nothing when the assistant is off."""
    from callobuzz_rag.api.schemas.widget import ProactiveGreetingOut
    from callobuzz_rag.conversation.schema import fill_persona, localized

    if runtime is None:
        return []
    cfg = runtime.config
    return [
        ProactiveGreetingOut(
            id=g.id,
            page_url_glob=g.page_url_glob,
            delay_seconds=g.delay_seconds,
            message=fill_persona(localized(g.message, cfg.language.primary), cfg),
            frequency=g.frequency,
        )
        for g in cfg.conversation.proactive_greetings
        if g.enabled
    ]


async def widget_reply(
    request: Request,
    session: "AsyncSession",
    corpus: "Corpus",
    key: Any,
    body: Any,
    runtime: RuntimeConfig,
    *,
    show_sources: bool,
    billing_paused: bool = False,
    paused_by: PausedBy | None = None,  # PIP3 gate
    profile: "EffectiveProfile | None" = None,  # PIP1 profile
    human_only: bool = False,  # TV1 human
) -> StreamingResponse:
    """A website visitor's message through the director. The chat is a stored conversation
    behind an opaque `ws_` session token; the server's own (masked) history replaces whatever
    the browser sends, so a visitor cannot plant earlier turns."""
    from callobuzz_rag.analytics.usage import widget_source
    from callobuzz_rag.conversation.envelope import Surface

    settings = request.app.state.settings
    conv, new_token = await widget_conversation(
        session,
        corpus_id=corpus.id,
        widget_key_id=key.id,
        token=body.session,
        ttl_hours=settings.assistant_session_ttl_hours,
        page_url=body.page_url,
        utm=body.utm,
        now=datetime.now(UTC),
    )
    await session.flush()  # a new conversation gets its id here
    conversation_id = conv.id
    await session.commit()
    state = ConversationState()
    history: list[LLMMessage] = []
    if new_token is None:
        conv, state, history = await conversation_context(
            session, corpus.id, conversation_id, settings
        )
    # A browser never picks the model: no X-LLM-* headers, the corpus's own config only.
    # PIP1 profile
    profile = profile or await tier_profiles.plan_profile(
        session, corpus.tenant_id, settings=settings
    )
    director = build_director(request, corpus, use_headers=False, options=None, profile=profile)
    runtime = runtime_with_profile(runtime, profile)
    # /PIP1 profile
    from callobuzz_rag.conversation import learning as site_learning  # BIZ4 learning

    turn = make_turn(
        corpus=corpus,
        runtime=runtime,
        text=body.message,
        channel=Channel.WIDGET,
        usage_source=widget_source(key.id),
        conversation_id=conversation_id,
        state=state,
        history=history,
        page_url=body.page_url,
        session_token=new_token,
        opts=None,
        one_off=None,
        widget_key_id=key.id,
        utm=body.utm or getattr(conv, "utm", None),
        billing_paused=billing_paused,  # BIL4 paused
        paused_by=paused_by,  # PIP3 gate
        learning=await site_learning.kb_is_learning(session, corpus.id),  # BIZ4 learning
        slot=slot_spec(request.app, profile),  # PIP1 lane
        human_only=human_only,  # TV1 human
    )
    await session.close()
    return StreamingResponse(
        sse_stream(director, turn, Surface.WIDGET, show_sources=show_sources),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def conversation_reply(
    request: Request,
    session: "AsyncSession",
    corpus: "Corpus",
    conversation_id: uuid.UUID,
    body: Any,
    runtime: RuntimeConfig,
    *,
    stream: bool,
    billing_paused: bool = False,
    paused_by: PausedBy | None = None,  # PIP3 gate
    profile: "EffectiveProfile | None" = None,  # PIP1 profile
    human_only: bool = False,  # TV1 human
) -> "MessageSendResponse | StreamingResponse":
    """A message in a stored conversation through the director, as JSON (both messages and
    the envelope) or as an SSE stream. The reply is retrieved through the orchestrator with the
    knowledge base's retrieval settings, with the history the conversation has always carried
    (its summary, then the recent messages within the token budget)."""
    from callobuzz_rag.analytics.usage import SOURCE_CONVERSATION
    from callobuzz_rag.conversation.director import DirectorError
    from callobuzz_rag.conversation.envelope import Surface
    from callobuzz_rag.db.models.message import Message

    conv, state, history = await conversation_context(
        session, corpus.id, conversation_id, request.app.state.settings
    )
    # PIP1 profile
    profile = profile or await tier_profiles.plan_profile(
        session, corpus.tenant_id, settings=request.app.state.settings
    )
    director = build_director(request, corpus, use_headers=True, options=None, profile=profile)
    runtime = runtime_with_profile(runtime, profile)
    # /PIP1 profile
    from callobuzz_rag.conversation import learning as site_learning  # BIZ4 learning

    turn = make_turn(
        corpus=corpus,
        runtime=runtime,
        text=body.content,
        channel=Channel.CONVERSATION,
        usage_source=SOURCE_CONVERSATION,
        conversation_id=conv.id,
        state=state,
        history=history,
        page_url=None,
        session_token=None,
        opts=body.assistant,
        one_off=None,
        billing_paused=billing_paused,  # BIL4 paused
        paused_by=paused_by,  # PIP3 gate
        learning=await site_learning.kb_is_learning(session, corpus.id),  # BIZ4 learning
        slot=slot_spec(request.app, profile),  # PIP1 lane
        human_only=human_only,  # TV1 human
    )
    if stream:
        await session.close()  # the stream writes through sessions of its own
        return StreamingResponse(
            sse_stream(director, turn, Surface.CONVERSATION),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    try:
        done = await director.run(turn)
    except DirectorError as exc:
        raise _api_error(exc) from exc
    finally:
        await director.aclose()
    if done.user_message_id is None or done.message_id is None:
        raise APIError(500, "Could not store the conversation turn", "conversation_not_stored")
    user = await session.get(Message, done.user_message_id)
    reply = await session.get(Message, done.message_id)
    return MessageSendResponse(
        user_message=MessageResponse.model_validate(user),
        assistant_message=MessageResponse.model_validate(reply),
        retrieval_context=getattr(reply, "retrieval_context", None),
        **reply_fields(done),
    )
