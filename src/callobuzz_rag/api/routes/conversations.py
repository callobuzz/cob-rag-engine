"""Conversation CRUD endpoints — corpus-scoped."""

import logging
from typing import TYPE_CHECKING
from uuid import UUID, uuid4, uuid5

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.analytics.usage import (
    SOURCE_CONVERSATION,
    estimate_tokens,
    payer_of,
    record_usage,
    record_usage_new_session,
    request_cost_of,
)
from callobuzz_rag.api.deps import get_session, path_corpus
from callobuzz_rag.api.routes.query import (
    _extract_llm_headers,
    _get_config_resolver,
    hold_slot_or_busy,  # PIP1 lane
    slotted_stream,  # PIP1 lane
)
from callobuzz_rag.api.schemas.conversations import (
    ConversationCreateRequest,
    ConversationListResponse,
    ConversationResponse,
    ConversationUpdateRequest,
    MessageListResponse,
    MessageResponse,
    MessageSendRequest,
    MessageSendResponse,
)
from callobuzz_rag.billing.context import mark_admin_answer  # BIL3 request id
from callobuzz_rag.billing.enforcement import payer_hint  # BIL4 billing
from callobuzz_rag.conversation import surfaces
from callobuzz_rag.conversation.types import Channel
from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.message import Message
from callobuzz_rag.fairuse.gate import answer_gate, pause_args, refuse_paused  # PIP3 gate
from callobuzz_rag.llm.models import LLMMessage
from callobuzz_rag.tiers import profiles as tier_profiles  # P5 plans

if TYPE_CHECKING:
    from callobuzz_rag.retrieval.search import HybridSearcher

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/corpora/{corpus_id}/conversations",
    tags=["conversations"],
)


@router.post("", response_model=ConversationResponse, status_code=201)
async def create_conversation(
    corpus_id: UUID,
    body: ConversationCreateRequest,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> ConversationResponse:
    """Create a new conversation in the corpus."""

    conversation = Conversation(
        corpus_id=corpus_id,
        title=body.title,
        created_by=body.created_by,
        metadata_=body.metadata,
    )
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)

    return ConversationResponse.model_validate(conversation)


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    corpus_id: UUID,
    created_by: str | None = Query(None),
    channel: Channel | None = Query(
        None,
        description=(
            "Only chats from this channel. Omitted: every channel except `widget` "
            "(website visitors' chats) and `whatsapp` (customers' chats on WhatsApp)."
        ),
    ),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> ConversationListResponse:
    """List conversations in the corpus, with optional created_by and channel filters.

    Website visitors' chats (`channel=widget`) and WhatsApp chats (`channel=whatsapp`, TV6) are
    left out unless asked for: they are customers' conversations, read on their own pages.
    """

    # COB chats (kind 'cob') belong to their author and are never listed here.
    where_clause = [Conversation.corpus_id == corpus_id, Conversation.kind == "assistant"]
    if created_by is not None:
        where_clause.append(Conversation.created_by == created_by)
    if channel is None:
        where_clause.append(
            Conversation.channel.notin_((Channel.WIDGET.value, Channel.WHATSAPP.value))  # TV6
        )
    else:
        where_clause.append(Conversation.channel == channel.value)

    result = await session.execute(
        select(Conversation)
        .where(*where_clause)
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    conversations = result.scalars().all()

    count_result = await session.execute(select(func.count(Conversation.id)).where(*where_clause))
    total = count_result.scalar() or 0

    return ConversationListResponse(
        items=[ConversationResponse.model_validate(c) for c in conversations],
        count=total,
        offset=offset,
        limit=limit,
    )


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    corpus_id: UUID,
    conversation_id: UUID,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> ConversationResponse:
    """Get a single conversation by ID."""

    result = await session.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.corpus_id == corpus_id,
            Conversation.kind == "assistant",
        )
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return ConversationResponse.model_validate(conversation)


@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    corpus_id: UUID,
    conversation_id: UUID,
    body: ConversationUpdateRequest,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> ConversationResponse:
    """Update conversation title and/or metadata."""

    result = await session.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.corpus_id == corpus_id,
            Conversation.kind == "assistant",
        )
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if body.title is not None:
        conversation.title = body.title
    if body.metadata is not None:
        conversation.metadata_ = body.metadata

    await session.commit()
    await session.refresh(conversation)

    return ConversationResponse.model_validate(conversation)


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    corpus_id: UUID,
    conversation_id: UUID,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a conversation and all its messages (CASCADE)."""

    result = await session.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.corpus_id == corpus_id,
            Conversation.kind == "assistant",
        )
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    await session.execute(delete(Conversation).where(Conversation.id == conversation_id))
    await session.commit()


@router.get(
    "/{conversation_id}/messages",
    response_model=MessageListResponse,
)
async def list_messages(
    corpus_id: UUID,
    conversation_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> MessageListResponse:
    """List messages in a conversation, oldest-first."""

    # Verify conversation exists and belongs to corpus
    conv_result = await session.execute(
        select(Conversation.id).where(
            Conversation.id == conversation_id,
            Conversation.corpus_id == corpus_id,
            Conversation.kind == "assistant",
        )
    )
    if conv_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .limit(limit)
        .offset(offset)
    )
    messages = result.scalars().all()

    count_result = await session.execute(
        select(func.count(Message.id)).where(Message.conversation_id == conversation_id)
    )
    total = count_result.scalar() or 0

    return MessageListResponse(
        items=[MessageResponse.model_validate(m) for m in messages],
        count=total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/{conversation_id}/messages",
    response_model=MessageSendResponse,
    status_code=201,
)
async def send_message(
    corpus_id: UUID,
    conversation_id: UUID,
    body: MessageSendRequest,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> MessageSendResponse | StreamingResponse:
    """Send a message in a conversation — retrieve context, generate, store.

    Returns both the user message and the AI-generated assistant response, or an
    SSE stream when the caller sends `Accept: text/event-stream`. FastAPI skips
    `response_model` validation for a Response instance, so the declared
    `response_model=MessageSendResponse` still describes the JSON branch.
    """

    surfaces.authorize_assistant_options(request, body.assistant)
    runtime = await surfaces.assistant_for(
        session, corpus, use_draft=bool(body.assistant and body.assistant.use_draft)
    )
    if surfaces.is_human_turn(runtime, body.content):  # TV1 human: before cap, billing, fair use
        assert runtime is not None
        return await surfaces.conversation_reply(
            request,
            session,
            corpus,
            conversation_id,
            body,
            runtime,
            stream="text/event-stream" in request.headers.get("accept", ""),
            human_only=True,
        )
    # /TV1 human

    # PIP3 gate: the monthly question cap (accounts A6), billing and fair use, before any work
    # P5 plans: the plan decides whether a stored or sent key pays (one profile query, shared
    # with the gate)
    plan = await tier_profiles.plan_profile(
        session, corpus.tenant_id, settings=request.app.state.settings
    )
    conv_payer = payer_hint(  # BIL4 billing
        corpus, request, use_headers=True, byok_allowed=plan.byok_allowed
    )
    # /P5 plans
    mark_admin_answer(request)  # BIL3 request id: the operator's own tests are not billed
    decision = await answer_gate(
        session,
        request,
        corpus,
        payer=conv_payer,
        surface="conversation",
        plan_profile=plan,  # P5 plans
    )
    # /PIP3 gate
    turn_id = uuid4()  # BIL3 request id: one id for everything this turn meters

    # Conversation layer (C1): with the assistant on, the director answers (retrieval through
    # the knowledge base's own pipeline, the persona and the two gates), the turn is stored
    # masked and metered at its real cost.
    if runtime is not None:
        return await surfaces.conversation_reply(
            request,
            session,
            corpus,
            conversation_id,
            body,
            runtime,
            stream="text/event-stream" in request.headers.get("accept", ""),
            **pause_args(decision),  # PIP3 gate
            profile=decision.profile,  # PIP3 gate
        )
    await refuse_paused(decision, request)  # PIP3 gate: the legacy path has no pause reply

    # The same key order as /query: X-LLM-* headers, then the corpus's own
    # llm_config (its encrypted key included), then the engine default. The
    # fast model (auto-title) resolves corpus -> engine default.
    from callobuzz_rag.llm.language_routing import route_answer_llm
    from callobuzz_rag.llm.litellm_provider import LiteLLMProvider

    # PIP1 profile: the business's pipeline profile over the knowledge base's settings
    from callobuzz_rag.tiers.resolve import resolve_with_profile

    llm_headers = _extract_llm_headers(request)
    engine_settings = request.app.state.settings
    profile = decision.profile  # PIP3 gate: the plan's profile, or the fallback when downgraded
    resolved_llm, _resolved_retrieval = resolve_with_profile(
        _get_config_resolver(request),
        corpus,
        headers=llm_headers or None,
        profile=profile,
        settings=engine_settings,
    )
    # /PIP1 profile
    llm_provider = LiteLLMProvider.from_resolved_config(resolved_llm)
    # Language routing: the answer is written by the model chosen for the visitor's language.
    llm_provider, routed_model = route_answer_llm(
        llm_provider, resolved_llm.language_models, body.content
    )
    answered_model = routed_model or resolved_llm.model

    # Build HybridSearcher if embedding + vector store are available
    embedder = getattr(request.app.state, "embedder", None)
    vector_store = getattr(request.app.state, "vector_store", None)

    def make_searcher(db: AsyncSession) -> "HybridSearcher | None":
        if embedder is None or vector_store is None:
            return None
        from callobuzz_rag.core.config import get_settings
        from callobuzz_rag.retrieval.embedding_cache import QueryEmbeddingCache
        from callobuzz_rag.retrieval.search import HybridSearcher

        settings = get_settings()
        # Resolve collection name from corpus
        collection = getattr(corpus, "qdrant_collection", settings.qdrant_collection)
        from callobuzz_rag.embeddings.factory import embedder_for_corpus

        corpus_embedder = embedder_for_corpus(corpus, settings, embedder) or embedder
        return HybridSearcher(
            vector_store=vector_store,
            embedding_provider=corpus_embedder,
            embedding_cache=QueryEmbeddingCache(settings),
            session=db,
            settings=settings,
            collection=collection,
        )

    # SSE streaming mode
    accept = request.headers.get("accept", "application/json")
    if "text/event-stream" in accept:
        from callobuzz_rag.api.services.streaming_service import stream_message_response

        session_factory = request.app.state.session_factory
        tenant_id = corpus.tenant_id

        async def _meter(messages: list[LLMMessage], reply: str) -> None:
            # Streamed replies carry no provider usage; estimated like
            # /query/stream, recorded in its own session like every stream.
            await record_usage_new_session(
                session_factory,
                corpus_id=corpus_id,
                tenant_id=tenant_id,
                source=SOURCE_CONVERSATION,
                prompt_tokens=sum(estimate_tokens(m.content) for m in messages),
                completion_tokens=estimate_tokens(reply),
                model=answered_model,
                cost=request_cost_of(llm_provider),
                payer=payer_of(resolved_llm),
                request_id=turn_id,  # BIL3 request id
            )

        async def _meter_failed() -> None:
            spent = request_cost_of(llm_provider)
            if spent is None:
                return
            await record_usage_new_session(
                session_factory,
                corpus_id=corpus_id,
                tenant_id=tenant_id,
                source=SOURCE_CONVERSATION,
                prompt_tokens=0,
                completion_tokens=0,
                model=answered_model,
                cost=spent,
                payer=payer_of(resolved_llm),
                requests=0,
                request_id=uuid5(turn_id, "failed"),  # BIL3 request id
            )

        # The stream opens its own session for as long as it runs; the
        # request's is released before the response starts.
        generator = stream_message_response(
            conversation_id=conversation_id,
            corpus_id=corpus_id,
            content=body.content,
            session_factory=session_factory,
            llm_provider=llm_provider,
            make_searcher=make_searcher,
            meter=_meter,
            meter_failed=_meter_failed,
        )
        await session.close()
        # PIP1 lane: the stream waits for an answer slot behind the same lanes
        return StreamingResponse(
            slotted_stream(request, profile, tenant_id, generator),
            media_type="text/event-stream",
        )
        # /PIP1 lane

    from callobuzz_rag.api.services.conversation_service import (
        ConversationNotFoundError,
        ConversationService,
    )

    service = ConversationService(
        session=session,
        llm_provider=llm_provider,
        searcher=make_searcher(session),
    )

    slot = await hold_slot_or_busy(request, profile, corpus.tenant_id)  # PIP1 lane
    try:
        user_msg, assistant_msg, retrieval_context = await service.send_message(
            conversation_id=conversation_id,
            corpus_id=corpus_id,
            content=body.content,
            metadata=body.metadata,
        )
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail="Conversation not found") from None
    finally:
        await slot.release()  # PIP1 lane

    # One request for the answer. The provider's ledger holds every call made for it (the
    # answer and the auto-title), so one row carries the whole request's real cost.
    spent = request_cost_of(llm_provider)
    if spent is not None:
        await record_usage(
            session,
            corpus_id=corpus_id,
            tenant_id=corpus.tenant_id,
            source=SOURCE_CONVERSATION,
            prompt_tokens=0,
            completion_tokens=0,
            model=answered_model,
            cost=spent,
            payer=payer_of(resolved_llm),
            commit=False,
            request_id=turn_id,  # BIL3 request id
        )
    else:
        # A provider with no ledger: the replies' own token counts, the title call adding
        # its tokens without counting as another request.
        for index, reply in enumerate(service.llm_responses):
            await record_usage(
                session,
                corpus_id=corpus_id,
                tenant_id=corpus.tenant_id,
                source=SOURCE_CONVERSATION,
                prompt_tokens=reply.usage.prompt_tokens,
                completion_tokens=reply.usage.completion_tokens,
                model=reply.model or resolved_llm.model,
                requests=1 if index == 0 else 0,
                payer=payer_of(resolved_llm),
                commit=False,
                request_id=turn_id if index == 0 else uuid5(turn_id, f"aux{index}"),  # BIL3
            )
    await session.commit()

    return MessageSendResponse(
        user_message=MessageResponse.model_validate(user_msg),
        assistant_message=MessageResponse.model_validate(assistant_msg),
        retrieval_context=retrieval_context,
    )
