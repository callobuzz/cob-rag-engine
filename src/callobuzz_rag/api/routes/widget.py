"""Public endpoints behind the embeddable chat widget.

    <script src="https://rag.example.com/widget.js" data-key="pk_..." async></script>

`GET /widget.js` serves the script. The script calls `GET /api/v1/widget/config`
for branding and `POST /api/v1/widget/chat` for answers, sending the pk_ key in
`X-Widget-Key`. These routes bypass the X-API-Key middleware; every one of them
authenticates the widget key, checks the browser Origin against the key's
allow-list and (for chat) applies the rate limits before doing any work.
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from importlib import resources
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask  # PIP1 lane

from callobuzz_rag.accounts.settings_store import powered_by_for
from callobuzz_rag.analytics.usage import (
    estimate_tokens,
    payer_of,
    record_usage_new_session,
    request_cost_of,
    widget_source,
)
from callobuzz_rag.api.deps import get_session
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.routes.query import (
    _build_orchestrator,
    _get_config_resolver,
    hold_slot_or_busy,  # PIP1 lane
)
from callobuzz_rag.api.schemas.handoffs import WidgetHandoffRequest, WidgetHandoffResponse
from callobuzz_rag.api.schemas.widget import (
    WidgetChatRequest,
    WidgetConfigResponse,
    WidgetEventRequest,
)
from callobuzz_rag.api.stage_events import (
    STAGE_UNDERSTAND,
    STAGE_WRITE,
    StageClock,
    events_until_done,
    stage_listener,
    stop_search,
)
from callobuzz_rag.api.streaming import shielded, sse_error, sse_event, sse_token
from callobuzz_rag.billing.context import mark_admin_answer  # BIL3 request id
from callobuzz_rag.billing.enforcement import payer_hint  # BIL4 billing
from callobuzz_rag.conversation import surfaces
from callobuzz_rag.conversation.handoff.channels import build_channels
from callobuzz_rag.conversation.handoff.service import create_handoff
from callobuzz_rag.conversation.human import is_human_request
from callobuzz_rag.conversation.sessions import resolve_widget_session
from callobuzz_rag.conversation.signals import office_state
from callobuzz_rag.conversation.state import load_state, save_state
from callobuzz_rag.core.product import POWERED_BY_TEXT
from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.handoff import Handoff
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.widget_key import WidgetKey
from callobuzz_rag.fairuse.gate import answer_gate, demo_limit_error, pause_args  # PIP3 gate
from callobuzz_rag.llm.language_routing import route_answer_llm
from callobuzz_rag.llm.models import LLMMessage
from callobuzz_rag.tiers import profiles as tier_profiles  # P5 plans
from callobuzz_rag.widget.security import (
    PUBLIC_KEY_PREFIX,
    WidgetRateLimiter,
    origin_allowed,
    request_client_ip,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["widget"])

# Only the last few turns are replayed to the model: enough for follow-up
# questions ("and how much is it?"), bounded so a long chat cannot inflate cost.
_MAX_HISTORY_TURNS = 6
_DEFAULTS = {
    "title": "Ask us anything",
    "greeting": "Hi! How can I help you today?",
    "placeholder": "Type your question…",
    "primary_color": "#4f46e5",
    "position": "right",
    "show_sources": True,
}


async def _authorize_widget(
    request: Request,
    session: AsyncSession,
    public_key: str | None,
    *,
    note_refused: bool = False,
) -> tuple[WidgetKey, Corpus]:
    if not public_key or not public_key.startswith(PUBLIC_KEY_PREFIX):
        raise HTTPException(status_code=401, detail="Widget key required")
    row = await session.execute(select(WidgetKey).where(WidgetKey.public_key == public_key))
    key = row.scalar_one_or_none()
    if key is None or not key.is_active:
        raise HTTPException(status_code=401, detail="Invalid or disabled widget key")
    if not origin_allowed(request.headers.get("origin"), list(key.allowed_origins)):
        # BIZ4 first seen: the config route remembers a refused site (the commonest install
        # mistake is a www twin), best effort, before the refusal below.
        if note_refused:
            await _note_refused_origin(request, session, key)
        # /BIZ4 first seen
        raise HTTPException(
            status_code=403, detail="This website is not allowed to use this widget key"
        )
    corpus = await session.get(Corpus, key.corpus_id)
    if corpus is None:
        raise HTTPException(status_code=401, detail="Invalid or disabled widget key")
    # A pending or suspended tenant (accounts A4). Website visitors must not learn that
    # a business is suspended: no `status` here, and widget.js shows it as a bot reply.
    tenant = await session.get(Tenant, corpus.tenant_id)
    if tenant is None or tenant.status != "active":
        raise APIError(403, "This assistant is not available right now.", "workspace_not_active")
    # P3 legal: keep the deployer's published privacy address fresh for the footer link and the
    # consent notice (cached, at most one small query every 30 s per process; never fails)
    from callobuzz_rag.legal import privacy_link

    try:
        await privacy_link.ensure(session, request.app.state.settings.public_url)
    except Exception:
        logger.warning("Could not refresh the privacy notice address", exc_info=True)
    # /P3 legal
    return key, corpus


# P3 legal
def _privacy_url(request: Request, runtime: Any) -> str | None:
    """The footer's privacy link: the business's own policy (it is the data fiduciary), else the
    deployer's published privacy notice, else none. It is chrome, so it does not depend on the
    assistant being on."""
    from callobuzz_rag.api.routes.widget_keys import engine_public_url
    from callobuzz_rag.legal.privacy_link import page_url

    own = runtime.config.leads.consent.privacy_policy_url if runtime is not None else None
    return own if isinstance(own, str) and own else page_url(engine_public_url(request))


# /P3 legal


# BIZ4 first seen
async def _note_refused_origin(request: Request, session: AsyncSession, key: WidgetKey) -> None:
    from callobuzz_rag.onboarding.service import note_widget_load

    origin = request.headers.get("origin")
    if not origin or not await _sighting_budget(request, key, origin):
        return
    await note_widget_load(
        getattr(request.app.state, "session_factory", None),
        key=key,
        corpus=None,
        origin=origin,
        allowed=False,
        settings=request.app.state.settings,
    )


async def _sighting_budget(request: Request, key: WidgetKey, origin: str) -> bool:
    """A refused site is written down at most once an hour per (key, site), and 30 distinct
    sites an hour per key: the Origin header is anyone's to forge, and each new one costs a
    database write. Over the budget, or with Redis down, nothing is written."""
    import hashlib

    try:
        limiter = _limiter(request)
        digest = hashlib.sha256(origin.encode()).hexdigest()[:16]
        fresh, _ = await limiter._hit(f"widget:seen:{key.id}:{digest}", 1, 3600)
        if not fresh:
            return False
        within, _ = await limiter._hit(f"widget:seen:{key.id}", 30, 3600)
        return bool(within)
    except Exception:
        logger.debug("sighting budget unavailable; not recording", exc_info=True)
        return False


# /BIZ4 first seen


# BIZ4 avatar
def absolute_avatar(value: str | None, base_url: str) -> str | None:
    """The engine's own avatar path becomes an address a customer's page can load."""
    if value is not None and value.startswith("/api/v1/public/avatars/"):
        return f"{base_url.rstrip('/')}{value}"
    return value


# /BIZ4 avatar


def _limiter(request: Request) -> WidgetRateLimiter:
    limiter: WidgetRateLimiter | None = getattr(request.app.state, "widget_rate_limiter", None)
    if limiter is None:
        limiter = WidgetRateLimiter(request.app.state.settings.redis_url)
        request.app.state.widget_rate_limiter = limiter
    return limiter


def _appearance(key: WidgetKey) -> dict[str, Any]:
    merged = dict(_DEFAULTS)
    merged.update({k: v for k, v in (key.appearance or {}).items() if v is not None})
    return merged


# `GET /widget.js` is these files joined, in this order, into one response: the Markdown renderer
# (`window.CobMd`) and the helpers first, then the widget that uses them. A new part is added here,
# never as a second <script> the customer would have to paste. A file that does not exist yet
# (widget-core.js, handoff.js: conversation C9, C5) is skipped, so a later epic only adds its file.
WIDGET_PARTS = (
    "static/markdown.js",
    "static/widget-core.js",
    "static/lead_form.js",
    "static/handoff.js",
    "static/sensor.js",  # BIZ2 sensor: window.CobSensor, called by widget.js after the config
    "static/widget.js",
)


@router.get("/widget.js", include_in_schema=False)
async def widget_script() -> Response:
    """The embeddable chat widget. Public, cacheable, no key needed to fetch it."""
    package = resources.files("callobuzz_rag.widget")
    files = (package.joinpath(part) for part in WIDGET_PARTS)
    script = "\n;\n".join(f.read_text("utf-8") for f in files if f.is_file())
    return Response(
        content=script,
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/api/v1/widget/config", response_model=WidgetConfigResponse)
async def widget_config(
    request: Request,
    session: AsyncSession = Depends(get_session),
    x_widget_key: str | None = Header(None),
) -> WidgetConfigResponse:
    """Branding for the widget, so it renders in the customer's colors and words."""
    key, corpus = await _authorize_widget(request, session, x_widget_key, note_refused=True)
    # BIZ4 first seen
    from callobuzz_rag.onboarding.service import note_widget_load

    await note_widget_load(
        getattr(request.app.state, "session_factory", None),
        key=key,
        corpus=corpus,
        origin=request.headers.get("origin"),
        allowed=True,
        settings=request.app.state.settings,
    )
    # /BIZ4 first seen
    runtime = await surfaces.assistant_for(session, corpus, use_draft=False)
    starters = list(runtime.config.conversation.starters) if runtime is not None else []
    assistant_fields = surfaces.widget_assistant_fields(runtime)
    # BIZ4 avatar
    from callobuzz_rag.api.routes.widget_keys import engine_public_url

    assistant_fields["avatar_url"] = absolute_avatar(
        assistant_fields["avatar_url"], engine_public_url(request)
    )
    # /BIZ4 avatar
    # BIZ2 sensor: tell the widget whether to report page fingerprints (never fails the config)
    sensor = None
    try:
        from callobuzz_rag.website import sensor as website_sensor

        sensor = await website_sensor.config_for(
            session, corpus, request.headers.get("origin"), request.app.state.settings
        )
    except Exception:
        logger.warning("Could not work out the widget sensor config", exc_info=True)
    # /BIZ2 sensor
    # TV1 human: the header button; an unreadable engine setting keeps it (hiding is opt-in)
    allow_hide = False
    require_disclosure, wording_editable = True, True  # TV5 disclosure: unreadable keeps it shown
    powered_by: dict[str, str] | None = {"text": POWERED_BY_TEXT}
    try:
        from callobuzz_rag.accounts.settings_store import get_engine_settings

        factory: Any = getattr(request.app.state, "session_factory", None)
        engine = await get_engine_settings(factory)
        allow_hide = engine.trust.allow_hide_human_button
        require_disclosure = engine.trust.require_ai_disclosure  # TV5 disclosure
        wording_editable = engine.trust.disclosure_wording_editable  # TV5 disclosure
        # Hidden only when Branding hides it; an unreadable setting keeps the line.
        shown = powered_by_for(engine.branding)
        powered_by = dict(shown) if shown is not None else None
    except Exception:
        logger.warning("Could not read the engine's trust settings for the widget", exc_info=True)
    # /TV1 human
    return WidgetConfigResponse(
        **_appearance(key),
        **assistant_fields,
        human_button=surfaces.widget_human_button(runtime, allow_hide=allow_hide),  # TV1 human
        **surfaces.widget_disclosure(  # TV5 disclosure
            runtime, required=require_disclosure, editable=wording_editable
        ),
        starters=starters,
        proactive=surfaces.widget_proactive(runtime),
        sensor=sensor,  # BIZ2 sensor
        powered_by=powered_by,
        privacy_url=_privacy_url(request, runtime),  # P3 legal
    )


async def _spend_handoff_bucket(request: Request, key: Any) -> None:
    """One hit on the visitor's handoff bucket (a card tap, or a chat message that asks for a
    person: TV1). 429 `handoff_rate_limited` when it is used up; fails open when Redis is down."""
    settings = request.app.state.settings
    try:
        ok, retry = await _limiter(request)._hit(
            f"widget:handoff:{key.id}:{request_client_ip(request)}",
            settings.handoff_rate_limit_per_hour,
            3600,
        )
    except Exception:
        logger.warning("Handoff rate limiter unavailable; allowing", exc_info=True)
        ok, retry = True, 0
    if not ok:
        raise APIError(
            429,
            "Too many requests. Please try again later.",
            "handoff_rate_limited",
            headers={"Retry-After": str(retry)},
        )


@router.post("/api/v1/widget/chat")
async def widget_chat(
    body: WidgetChatRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    x_widget_key: str | None = Header(None),
) -> StreamingResponse:
    """Answer a website visitor from the key's corpus, streamed as Server-Sent Events.

    Events: `stage` ({stage, elapsed_ms}) while the assistant works, `sources`
    ({sources: [{file_name, page, snippet}]}), then `token` ({content}) repeatedly,
    then `done`. `error` ({detail}) replaces the rest if searching or generation fails.
    """
    key, corpus = await _authorize_widget(request, session, x_widget_key)

    # TV1 human: a visitor asking for a person is let through before the chat limits, the
    # question cap, billing and fair use; it spends the handoff bucket, never the chat quota.
    # The pure regex runs first so a flood of ordinary messages costs no config query before
    # the limiter below.
    if is_human_request(body.message):
        runtime = await surfaces.assistant_for(session, corpus, use_draft=False)
        if surfaces.is_human_turn(runtime, body.message):
            assert runtime is not None
            await _spend_handoff_bucket(request, key)
            return await surfaces.widget_reply(
                request,
                session,
                corpus,
                key,
                body,
                runtime,
                show_sources=bool(_appearance(key)["show_sources"]),
                human_only=True,
            )
    # /TV1 human

    limit = await _limiter(request).check(
        str(key.id),
        request_client_ip(request),
        key.rate_limit_per_minute,
        key.daily_message_limit,
    )
    if not limit.allowed:
        raise HTTPException(
            status_code=429, detail=limit.reason, headers={"Retry-After": str(limit.retry_after)}
        )
    # PIP3 gate: the tenant's monthly question cap (accounts A6, a polite sentence, no plan
    # details), billing and fair use. A pause is the business's own pause message and handoff
    # card; the widget never says a word about a plan, a limit or a balance.
    # P5 plans: the plan decides whether the knowledge base's stored key pays (shared with the gate)
    plan = await tier_profiles.plan_profile(
        session, corpus.tenant_id, settings=request.app.state.settings
    )
    widget_payer = payer_hint(  # BIL4 billing: no browser key
        corpus, None, use_headers=False, byok_allowed=plan.byok_allowed
    )
    # /P5 plans
    mark_admin_answer(request)  # BIL3 request id
    decision = await answer_gate(
        session,
        request,
        corpus,
        payer=widget_payer,
        surface="widget",
        plan_profile=plan,  # P5 plans
    )
    # /PIP3 gate
    turn_id = uuid.uuid4()  # BIL3 request id: the failed and the finished meter share it
    runtime = await surfaces.assistant_for(session, corpus, use_draft=False)

    # Conversation layer (C1): with the assistant on, the visitor's chat is a stored, masked
    # conversation behind a session token and the director answers it.
    if runtime is not None:
        return await surfaces.widget_reply(
            request,
            session,
            corpus,
            key,
            body,
            runtime,
            show_sources=bool(_appearance(key)["show_sources"]),
            **pause_args(decision),  # PIP3 gate
            profile=decision.profile,  # PIP3 gate
        )
    if decision.action == "pause":  # no pause reply on the legacy path, and no word of why
        if decision.paused_by == "demo_limit":  # L5a demo: the polite demo sentence
            raise demo_limit_error(request)
        raise APIError(409, "This assistant is not available right now.", "assistant_disabled")

    # The corpus's own configuration only. A browser is never allowed to pick
    # the model or supply credentials, so no X-LLM-* header is read here.
    resolver = _get_config_resolver(request)
    # PIP1 profile: the business's pipeline profile over the knowledge base's settings
    from callobuzz_rag.tiers.resolve import resolve_with_profile

    engine_settings = request.app.state.settings
    profile = decision.profile  # PIP3 gate: the plan's profile, or the fallback when downgraded
    resolved_llm, resolved_retrieval = resolve_with_profile(
        resolver,
        corpus,
        headers=None,
        profile=profile,
        settings=engine_settings,
    )
    # /PIP1 profile
    resolved_prompt = resolver.resolve_prompt(
        request_body=None, corpus_prompt_config=corpus.prompt_config
    )
    orchestrator = _build_orchestrator(
        request,
        session,
        corpus,
        resolved_llm,
        resolved_prompt,
        resolved_retrieval=resolved_retrieval,
    )

    show_sources = bool(_appearance(key)["show_sources"])
    # PIP1 lane: a busy engine reads as the widget's one "not available" sentence, never a
    # word about queues or limits. The slot is held until the stream ends.
    try:
        slot = await hold_slot_or_busy(request, profile, corpus.tenant_id)
    except APIError as exc:
        if exc.code != "answers_busy":
            raise
        raise APIError(
            409, "This assistant is not available right now.", "assistant_disabled"
        ) from exc
    # /PIP1 lane
    llm = orchestrator.llm
    answer_llm, routed_model = route_answer_llm(llm, resolved_llm.language_models, body.message)
    answered_model = routed_model or resolved_llm.model
    session_factory = getattr(request.app.state, "session_factory", None)

    async def _generate() -> AsyncIterator[str]:
        # The pipeline runs inside the stream so `stage` events reach the browser while it
        # works (the widget turns a long wait into "Searching your documents...").
        clock = StageClock()
        queue: asyncio.Queue[str] = asyncio.Queue()
        first = clock.enter(STAGE_UNDERSTAND)
        if first is not None:
            yield first
        task = asyncio.ensure_future(
            orchestrator.run_retrieval_pipeline(
                text=body.message,
                corpus_id=corpus.id,
                **surfaces.retrieval_kwargs(None, resolved_retrieval),
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
        except Exception:
            logger.exception("Widget retrieval failed for corpus %s", corpus.id)
            yield sse_error("The assistant could not search its documents")
            return
        finally:
            await stop_search(task)  # the visitor left mid-search: let the search unwind first
            # Release the request's connection before generating; metering below opens
            # its own session.
            await shielded(session.close())

        # Replay recent turns between the system prompt and the grounded question.
        messages = list(pipeline.assembled.messages)
        history = [
            LLMMessage(role=turn.role, content=turn.content)
            for turn in body.history[-_MAX_HISTORY_TURNS:]
        ]
        if history and messages:
            messages = messages[:-1] + history + messages[-1:]

        if show_sources:
            sources = [
                {
                    "index": chunk.citation_index,
                    "file_name": chunk.file_name,
                    "section": chunk.section,
                    "page": chunk.page,
                    "snippet": chunk.text[:240],
                }
                for chunk in pipeline.assembled.cited_chunks
            ]
            yield sse_event("sources", {"sources": sources})
        write_event = clock.enter(STAGE_WRITE)
        if write_event is not None:
            yield write_event
        parts: list[str] = []
        try:
            async for piece in answer_llm.complete_stream(messages):
                parts.append(piece)
                yield sse_token(piece)
        except Exception:
            logger.exception("Widget generation failed for corpus %s", corpus.id)
            # The helper calls and the partial answer were paid for: meter that spend (there
            # is no answered question to count).
            spent = request_cost_of(llm)
            if spent is not None:
                await shielded(
                    record_usage_new_session(
                        session_factory,
                        corpus_id=corpus.id,
                        tenant_id=corpus.tenant_id,
                        source=widget_source(key.id),
                        prompt_tokens=0,
                        completion_tokens=0,
                        model=answered_model,
                        cost=spent,
                        payer=payer_of(resolved_llm),
                        requests=0,
                        request_id=turn_id,  # BIL3 request id
                    )
                )
            yield sse_error("The assistant could not answer right now. Please try again.")
            return
        # Metered before `done`, in its own session and shielded: a browser
        # that closes the stream on `done` must not cut the write off.
        await shielded(
            record_usage_new_session(
                session_factory,
                corpus_id=corpus.id,
                tenant_id=corpus.tenant_id,
                source=widget_source(key.id),
                prompt_tokens=sum(estimate_tokens(m.content) for m in messages),
                completion_tokens=estimate_tokens("".join(parts)),
                model=answered_model,
                cost=request_cost_of(llm),
                payer=payer_of(resolved_llm),
                request_id=turn_id,  # BIL3 request id
            )
        )
        yield sse_event("done", {})

    async def _held() -> AsyncIterator[str]:
        try:
            async for frame in _generate():  # PIP1 lane: the slot is freed however it ends
                yield frame
        finally:
            await slot.release()

    return StreamingResponse(
        _held(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        background=BackgroundTask(slot.release),  # PIP1 lane: a stream that never started
    )


@router.post("/api/v1/widget/event", status_code=204)
async def widget_event(
    body: WidgetEventRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    x_widget_key: str | None = Header(None),
) -> Response:
    """Record a chip tap or a call-to-action click or dismissal for this chat.

    A dismissed call to action is never offered again in the same chat. Counters only; limited
    by its own per-visitor window, and it never spends the daily message quota.
    """
    key, corpus = await _authorize_widget(request, session, x_widget_key)
    settings = request.app.state.settings
    limit = await _limiter(request).check_event(
        str(key.id), request_client_ip(request), key.rate_limit_per_minute
    )
    if not limit.allowed:
        raise HTTPException(
            status_code=429, detail=limit.reason, headers={"Retry-After": str(limit.retry_after)}
        )
    runtime = await surfaces.assistant_for(session, corpus, use_draft=False)
    if runtime is None:
        raise APIError(409, "This assistant is not available right now.", "assistant_disabled")
    conversation = await resolve_widget_session(
        session,
        widget_key_id=key.id,
        token=body.session,
        ttl_hours=settings.assistant_session_ttl_hours,
        now=datetime.now(UTC),
    )
    if conversation is None or getattr(conversation, "corpus_id", corpus.id) != corpus.id:
        raise APIError(
            400, "This chat has expired. Please refresh the page.", "widget_session_invalid"
        )
    # A chat turn may be writing the same state (a tap fires this and the next message at
    # once): lock the row, read it, and write a new dict so the JSONB change is detected.
    await session.refresh(conversation, with_for_update=True)
    state = load_state(conversation)
    if body.type == "cta_dismiss" and body.cta_type is not None:
        state.decline_cta(body.cta_type)
    elif body.type == "cta_click" and body.cta_type is not None:
        state.record_cta_click(body.cta_type)
    else:
        state.record_chip_click()
    # A tap is not activity: pin updated_at so it neither extends the session's lifetime nor
    # reorders the conversation list.
    await session.execute(
        update(Conversation)
        .where(Conversation.id == conversation.id)
        .values(assistant_state=state.to_dict(), updated_at=Conversation.updated_at)
    )
    await session.commit()
    return Response(status_code=204)


_REPEAT_WINDOW = timedelta(minutes=10)


async def _recent_handoff(
    session: AsyncSession, conversation_id: Any, channel: str
) -> Handoff | None:
    """The handoff this conversation already made on this channel in the last ten minutes: a
    second tap (a double click, a retry) is the same handoff, not a second notification."""
    row = await session.execute(
        select(Handoff)
        .where(
            Handoff.conversation_id == conversation_id,
            Handoff.channel == channel,
            Handoff.created_at > datetime.now(UTC) - _REPEAT_WINDOW,
        )
        .order_by(Handoff.created_at.desc())
        .limit(1)
    )
    return row.scalars().first()


@router.post("/api/v1/widget/handoff", response_model=WidgetHandoffResponse, status_code=201)
async def widget_handoff(
    body: WidgetHandoffRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    x_widget_key: str | None = Header(None),
) -> WidgetHandoffResponse:
    """The visitor tapped a way to reach a person on the handoff card: tell the business.

    Not an answer: no question cap, no usage row, and a bucket of its own so a tap never spends
    the chat quota. Which channels the card *offered* depends on office hours; whether a tap is
    *recorded* does not (a card shown at 17:59 is tapped at 18:01), only on the channel being
    enabled and usable.
    """
    key, corpus = await _authorize_widget(request, session, x_widget_key)
    settings = request.app.state.settings
    await _spend_handoff_bucket(request, key)
    runtime = await surfaces.assistant_for(session, corpus, use_draft=False)
    if runtime is None:
        raise APIError(409, "This assistant cannot connect you right now.", "assistant_disabled")
    config = runtime.config
    if not config.handoff.enabled:
        raise APIError(409, "This assistant cannot connect you right now.", "handoff_disabled")
    now = datetime.now(UTC)
    conversation = await resolve_widget_session(
        session,
        widget_key_id=key.id,
        token=body.session,
        ttl_hours=settings.assistant_session_ttl_hours,
        now=now,
    )
    if conversation is None or getattr(conversation, "corpus_id", corpus.id) != corpus.id:
        raise APIError(
            400, "This chat has expired. Please refresh the page.", "widget_session_invalid"
        )
    office = office_state(config.identity.card, now)
    usable = {
        c.kind
        for c in build_channels(
            config,
            office,
            question="",
            visitor_name=None,
            language="en",
            ignore_availability=True,
        )
    }
    if body.channel not in usable:
        raise APIError(
            409,
            "That way of reaching us is not available right now.",
            "handoff_channel_unavailable",
            channel=body.channel,
        )
    # A chat turn may be writing the same state: lock the row, read it, write a new dict.
    await session.refresh(conversation, with_for_update=True)
    existing = await _recent_handoff(session, conversation.id, body.channel)
    if existing is not None:
        return WidgetHandoffResponse(handoff_id=existing.id)
    state = load_state(conversation)
    row = await create_handoff(
        session,
        corpus_id=corpus.id,
        tenant_id=corpus.tenant_id,
        conversation_id=conversation.id,
        channel=body.channel,
        reason=state.last_handoff_reason or "explicit_request",
        language=conversation.language,
        page_url=conversation.page_url,
        office=office,
    )
    state.record_handoff(str(row.id))
    save_state(conversation, state)
    await session.commit()
    # Deferred: the worker module imports Celery and the whole task set.
    from callobuzz_rag.worker.conversation_tasks import queue_handoff_notification

    queue_handoff_notification(row.id)
    return WidgetHandoffResponse(handoff_id=row.id)
