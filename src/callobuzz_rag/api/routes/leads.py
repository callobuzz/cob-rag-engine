"""Leads: the widget's lead form, the visitor's withdrawal, and the leads inbox (C4)."""

import logging
import secrets
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from importlib import resources
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import record_audit
from callobuzz_rag.api.deps import (
    authorized_corpus_ids,
    get_session,
    require_owner,
    resolve_scope,
)
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.routes.widget import _authorize_widget, _limiter
from callobuzz_rag.api.schemas.leads import (
    LeadConfirmation,
    LeadConsentOut,
    LeadContact,
    LeadDetail,
    LeadListResponse,
    LeadSummary,
    LeadUpdateRequest,
    TranscriptMessage,
    WidgetLeadRequest,
    WidgetLeadResponse,
    WithdrawRequest,
    WithdrawResponse,
)
from callobuzz_rag.conversation.handoff.channels import build_channels
from callobuzz_rag.conversation.handoff.reference import refs_for  # TV2 reference
from callobuzz_rag.conversation.handoff.service import create_handoff, validate_callback_window
from callobuzz_rag.conversation.leads.consent import notice_for
from callobuzz_rag.conversation.leads.crypto import (
    LeadCryptoUnavailableError,
    lead_crypto_from_settings,
)
from callobuzz_rag.conversation.leads.export import iter_csv, lead_row
from callobuzz_rag.conversation.leads.service import (
    LeadFilters,
    NewLead,
    _filter_clauses,
    create_lead,
    erase_lead,
    lead_fields,
    list_leads,
    update_lead,
)
from callobuzz_rag.conversation.leads.tokens import (
    WithdrawTokenError,
    make_withdraw_token,
    new_withdraw_secret,
    read_withdraw_token,
    secret_hash,
    secret_matches,
    withdraw_url,
)
from callobuzz_rag.conversation.leads.validation import LeadValidationError, validate_submission
from callobuzz_rag.conversation.sessions import resolve_widget_session
from callobuzz_rag.conversation.signals import office_state
from callobuzz_rag.conversation.state import load_state, save_state
from callobuzz_rag.conversation.store import get_runtime_config
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.lead import Lead
from callobuzz_rag.db.models.lead_consent import LeadConsent as LeadConsentRow
from callobuzz_rag.db.models.message import Message
from callobuzz_rag.widget.security import request_client_ip
from callobuzz_rag.worker.conversation_tasks import (
    queue_handoff_notification,
    queue_lead_notification,
)

logger = logging.getLogger(__name__)

widget_router = APIRouter(tags=["widget"])
public_router = APIRouter(tags=["leads"])
router = APIRouter(prefix="/api/v1/leads", tags=["leads"])

_THANKS = {
    "en": "Thank you! Our team will contact you soon.",
    "hi": "धन्यवाद! हमारी टीम जल्द ही आपसे संपर्क करेगी।",
    "hi-Latn": "Shukriya! Hamari team jald hi aapse sampark karegi.",
}


def _validation_error(e: LeadValidationError) -> APIError:
    messages = {
        "lead_invalid_phone": "Please enter a valid mobile number.",
        "lead_invalid_email": "Please enter a valid e-mail address.",
        "lead_field_invalid": "Please check this field.",
    }
    return APIError(422, messages[e.code], e.code, field=e.field, reason=e.reason)


def _public_base(request: Request) -> str:
    """The base of withdrawal links: RAG_PUBLIC_URL. Behind a proxy the request's own base
    URL is an internal host, so a link built from it would not open for the visitor."""
    configured: str = request.app.state.settings.public_url
    if configured:
        return configured
    logger.warning("RAG_PUBLIC_URL is not set: withdrawal links use this request's host")
    return str(request.base_url)


def _confirmation(
    config: Any, language: str, url: str, booking_url: str | None = None
) -> LeadConfirmation:
    texts = config.leads.confirmation_message or {}
    message = texts.get(language) or texts.get("en") or _THANKS.get(language, _THANKS["en"])
    return LeadConfirmation(message=message, withdraw_url=url, booking_url=booking_url)


def _booking(config: Any, lead: Lead | None) -> str | None:
    """Qualified leads are sent straight to booking when the owner set a link."""
    if lead is None or lead.qualification != "qualified":
        return None
    url: str | None = config.identity.card.booking_url
    return url


@widget_router.post("/api/v1/widget/lead", response_model=WidgetLeadResponse, status_code=201)
async def submit_lead(
    body: WidgetLeadRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    x_widget_key: str | None = Header(None),
) -> WidgetLeadResponse:
    """A visitor's lead form. Leads never count as questions (design section 7): no cap check,
    no usage row."""
    key, corpus = await _authorize_widget(request, session, x_widget_key)
    settings = request.app.state.settings
    ip = request_client_ip(request)
    try:
        ok, retry = await _limiter(request)._hit(
            f"widget:lead:{key.id}:{ip}", settings.lead_rate_limit_per_hour, 3600
        )
    except Exception:
        logger.warning("Lead rate limiter unavailable; allowing", exc_info=True)
        ok, retry = True, 0
    if not ok:
        raise APIError(
            429,
            "Too many requests. Please try again later.",
            "lead_rate_limited",
            headers={"Retry-After": str(retry)},
        )

    if body.website.strip():
        # A bot filled the hidden field: answer as if it worked, store nothing.
        logger.info("Lead honeypot filled for widget key %s; ignored", key.id)
        return WidgetLeadResponse(
            confirmation=LeadConfirmation(
                message=_THANKS["en"],
                withdraw_url=withdraw_url(_public_base(request), secrets.token_urlsafe(96)),
            )
        )

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
    runtime = await get_runtime_config(session, corpus.id)
    if runtime is None or not runtime.enabled:
        raise APIError(409, "This assistant is not taking details right now.", "assistant_disabled")
    config = runtime.config
    if not config.leads.enabled:
        raise APIError(409, "This assistant is not taking details right now.", "leads_disabled")
    try:
        crypto = lead_crypto_from_settings(settings)
    except LeadCryptoUnavailableError:
        raise APIError(
            503, "This assistant cannot take details right now.", "encryption_not_configured"
        ) from None

    # A chat turn may be writing the same state: lock the row, then read it.
    await session.refresh(conversation, with_for_update=True)
    state = load_state(conversation)
    language = conversation.language or config.language.primary
    if state.lead_id:
        # Submitted twice (a double click, a retry): no second lead. Only the hash of a link's
        # secret is kept, so this answer carries a fresh link (the earlier one stops working).
        secret = new_withdraw_secret()
        existing = await session.get(Lead, uuid.UUID(state.lead_id))
        if existing is not None:
            existing.withdraw_hash = secret_hash(secret)
            await session.commit()
        url = withdraw_url(
            _public_base(request), make_withdraw_token(uuid.UUID(state.lead_id), secret)
        )
        booking = _booking(config, existing)
        return WidgetLeadResponse(confirmation=_confirmation(config, language, url, booking))
    shown = f"{body.form_id}:{config.leads.consent.notice_version}"
    if shown not in state.lead_form_ids:
        # Unknown, used up, or shown with a notice the owner has since republished: the
        # consent record must say what the visitor actually saw.
        raise APIError(400, "This form has expired. Please ask again.", "widget_session_invalid")
    if not body.consent:
        raise APIError(422, "Please tick the box to agree before sending.", "lead_consent_required")
    try:
        clean = validate_submission(config.leads, body.fields)
    except LeadValidationError as e:
        raise _validation_error(e) from None

    # A callback request (the handoff card's form): its own checks, a tag, and a handoff.
    # Whether a callback is offered right now (office hours) is not asked: a form shown at
    # 17:59 is sent at 18:01. It must be enabled, and leads are on (checked above).
    callback = body.callback_window
    office = office_state(config.identity.card, now)
    if callback is not None:
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
        if not config.handoff.enabled or "callback" not in usable:
            raise APIError(
                409,
                "Callbacks are not available right now.",
                "handoff_channel_unavailable",
                channel="callback",
            )
        validate_callback_window(callback, config.identity.card, now)

    secret = new_withdraw_secret()
    lead = await create_lead(
        session,
        crypto,
        config,
        NewLead(
            corpus_id=corpus.id,
            tenant_id=corpus.tenant_id,
            conversation_id=conversation.id,
            widget_key_id=key.id,
            channel="widget",
            clean=clean,
            language=language,
            page_url=conversation.page_url,
            utm=conversation.utm,
            trigger="callback"
            if callback is not None
            else (state.lead_offer_reason or "lead_form"),
            source_label=None,
            tags=["callback"] if callback is not None else [],
            callback_window=callback.model_dump(mode="json") if callback is not None else None,
            sources_consulted=[],
            notice_text=notice_for(config, language),
            ip=ip,
            withdraw_hash=secret_hash(secret),
        ),
        now=now,
    )
    state.lead_id = str(lead.id)
    state.lead_form_ids = [f for f in state.lead_form_ids if f != shown]
    handoff = None
    if callback is not None:
        handoff = await create_handoff(
            session,
            corpus_id=corpus.id,
            tenant_id=corpus.tenant_id,
            conversation_id=conversation.id,
            channel="callback",
            reason=state.last_handoff_reason or "explicit_request",
            language=language,
            page_url=conversation.page_url,
            office=office,
            lead_id=lead.id,
        )
        state.record_handoff(str(handoff.id))
    save_state(conversation, state)
    await session.commit()
    queue_lead_notification(lead.id)
    if handoff is not None:
        queue_handoff_notification(handoff.id)
    url = withdraw_url(_public_base(request), make_withdraw_token(lead.id, secret))
    return WidgetLeadResponse(
        confirmation=_confirmation(config, language, url, _booking(config, lead))
    )


# ---- the visitor's withdrawal -----------------------------------------------------------

_SYSTEM_ACTOR = ("system", None, "visitor withdrawal")


def _static(name: str) -> str:
    return (
        resources.files("callobuzz_rag.conversation").joinpath(f"static/{name}").read_text("utf-8")
    )


@public_router.get("/privacy/withdraw", include_in_schema=False)
async def withdraw_page() -> Response:
    """The page a withdrawal link opens. The token stays in the URL fragment."""
    return Response(
        _static("withdraw.html"),
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300", "Referrer-Policy": "no-referrer"},
    )


@public_router.get("/privacy/withdraw.js", include_in_schema=False)
async def withdraw_script() -> Response:
    return Response(
        _static("withdraw.js"),
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300"},
    )


@public_router.post("/api/v1/public/leads/withdraw", response_model=WithdrawResponse)
async def withdraw_lead(
    body: WithdrawRequest, request: Request, session: AsyncSession = Depends(get_session)
) -> WithdrawResponse:
    """A visitor withdraws consent: the lead is erased, with the widget chat it came from.
    Idempotent: a second call with the same link answers the same."""
    ip = request_client_ip(request)
    try:
        ok, retry = await _limiter(request)._hit(f"public:withdraw:{ip}", 10, 3600)
    except Exception:
        logger.warning("Withdraw rate limiter unavailable; allowing", exc_info=True)
        ok, retry = True, 0
    if not ok:
        raise APIError(
            429,
            "Too many tries. Please try again later.",
            "lead_rate_limited",
            headers={"Retry-After": str(retry)},
        )
    try:
        lead_id, secret = read_withdraw_token(body.token)
    except WithdrawTokenError:
        raise APIError(
            400, "This link has expired or is not valid.", "withdraw_token_invalid"
        ) from None
    lead = await session.get(Lead, lead_id)
    if lead is not None:
        if not secret_matches(lead.withdraw_hash, secret):
            raise APIError(400, "This link has expired or is not valid.", "withdraw_token_invalid")
        corpus_id = lead.corpus_id
        tenant_id = lead.tenant_id
        await erase_lead(session, lead, withdrawn=True)
        await record_audit(
            session,
            request,
            "lead.withdrawn",
            tenant_id=tenant_id,
            target_type="lead",
            target_id=str(lead_id),
            details={"corpus_id": str(corpus_id), "lead_id": str(lead_id)},
            actor=_SYSTEM_ACTOR,
        )
        await session.commit()
    return WithdrawResponse()


# ---- the inbox --------------------------------------------------------------------------
# Every response below carries contact data: none of it may be cached by a browser or a proxy.

_NO_STORE = {"Cache-Control": "no-store"}
_STATUS = "^(new|contacted|won|lost|spam)$"
_QUALIFICATION = "^(qualified|partial|unqualified)$"


def _crypto(request: Request) -> Any:
    try:
        return lead_crypto_from_settings(request.app.state.settings)
    except LeadCryptoUnavailableError:
        raise APIError(
            503, "Leads need RAG_ENCRYPTION_KEY on this engine.", "encryption_not_configured"
        ) from None


def _filters(
    corpus_id: UUID | None,
    status: str | None,
    trigger: str | None,
    date_from: date | None,
    date_to: date | None,
    q: str | None,
    page_url: str | None,
    qualification: str | None = None,
) -> LeadFilters:
    return LeadFilters(
        corpus_id=corpus_id,
        status=status,
        trigger=trigger,
        date_from=date_from,
        date_to=date_to,
        q=q,
        page_url=page_url,
        qualification=qualification,
    )


async def _corpus_names(session: AsyncSession, ids: set[UUID]) -> dict[UUID, str]:
    if not ids:
        return {}
    rows = await session.execute(select(Corpus.id, Corpus.name).where(Corpus.id.in_(ids)))
    return {r.id: r.name for r in rows.all()}


def _summary(lead: Lead, fields: dict[str, Any], kb: str) -> LeadSummary:
    return LeadSummary(
        id=lead.id,
        corpus_id=lead.corpus_id,
        knowledge_base=kb,
        contact=LeadContact(**fields),
        status=lead.status,
        qualification=lead.qualification,
        trigger=lead.trigger,
        tags=list(lead.tags or []),
        language=lead.language,
        page_url=lead.page_url,
        assignee_user_id=lead.assignee_user_id,
        created_at=lead.created_at,
    )


async def _scoped_lead(request: Request, session: AsyncSession, lead_id: UUID) -> Lead:
    allowed = await authorized_corpus_ids(resolve_scope(request), None, session)
    lead = await session.get(Lead, lead_id)
    if lead is None or (allowed is not None and lead.corpus_id not in allowed):
        raise APIError(404, "Lead not found", "lead_not_found")
    return lead


@router.get("", response_model=LeadListResponse)
async def list_leads_route(
    request: Request,
    response: Response,
    corpus_id: UUID | None = Query(None),
    status: str | None = Query(None, pattern=_STATUS),
    trigger: str | None = Query(None, max_length=32),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    q: str | None = Query(None, max_length=254, description="A phone number or e-mail, exact"),
    page_url: str | None = Query(None, max_length=200),
    qualification: str | None = Query(None, pattern=_QUALIFICATION),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> LeadListResponse:
    """The leads of the knowledge bases the caller may see, newest first."""
    crypto = _crypto(request)
    allowed = await authorized_corpus_ids(resolve_scope(request), corpus_id, session)
    leads, total = await list_leads(
        session,
        corpus_ids=allowed,
        filters=_filters(
            corpus_id, status, trigger, date_from, date_to, q, page_url, qualification
        ),
        crypto=crypto,
        offset=offset,
        limit=limit,
    )
    names = await _corpus_names(session, {lead.corpus_id for lead in leads})
    items = [
        _summary(lead, lead_fields(crypto, lead), names.get(lead.corpus_id, "")) for lead in leads
    ]
    response.headers.update(_NO_STORE)
    return LeadListResponse(items=items, total=total, offset=offset, limit=limit)


async def _count_for_export(
    session: AsyncSession, crypto: Any, allowed: Sequence[UUID] | None, filters: LeadFilters
) -> int:
    clauses = _filter_clauses(crypto, filters)
    if allowed is not None:
        clauses.append(Lead.corpus_id.in_(list(allowed)))
    return int((await session.execute(select(func.count(Lead.id)).where(*clauses))).scalar() or 0)


async def _export_tenant(session: AsyncSession, scope: Any, corpus_id: UUID | None) -> UUID | None:
    """The tenant an export is filed under: the caller's own, else the asked-for knowledge
    base's, else none (an admin exporting across tenants). Never a lead's: an empty export
    would then have none, and a mixed one would belong to whichever lead came first."""
    if scope.tenant_id is not None:
        return scope.tenant_id  # type: ignore[no-any-return]
    if corpus_id is not None:
        corpus = await session.get(Corpus, corpus_id)
        return corpus.tenant_id if corpus is not None else None
    return None


@router.get("/export.csv")
async def export_leads_route(
    request: Request,
    corpus_id: UUID | None = Query(None),
    status: str | None = Query(None, pattern=_STATUS),
    trigger: str | None = Query(None, max_length=32),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    q: str | None = Query(None, max_length=254),
    page_url: str | None = Query(None, max_length=200),
    qualification: str | None = Query(None, pattern=_QUALIFICATION),
    session: AsyncSession = Depends(get_session),
    _owner: None = Depends(require_owner),
) -> StreamingResponse:
    """The same filters as the list, as a CSV for a spreadsheet. Owners only; audited."""
    crypto = _crypto(request)
    settings = request.app.state.settings
    scope = resolve_scope(request)
    allowed = await authorized_corpus_ids(scope, corpus_id, session)
    filters = _filters(corpus_id, status, trigger, date_from, date_to, q, page_url, qualification)
    count = await _count_for_export(session, crypto, allowed, filters)
    if count > settings.lead_export_max_rows:
        raise APIError(
            413,
            f"Too many leads to export at once ({count}); narrow the filters.",
            "export_too_large",
            limit=settings.lead_export_max_rows,
        )
    leads, _ = await list_leads(
        session,
        corpus_ids=allowed,
        filters=filters,
        crypto=crypto,
        offset=0,
        limit=settings.lead_export_max_rows,
    )
    names = await _corpus_names(session, {lead.corpus_id for lead in leads})
    refs = await refs_for(session, [lead.conversation_id for lead in leads])  # TV2 reference
    rows = [
        lead_row(
            lead,
            lead_fields(crypto, lead),
            names.get(lead.corpus_id, ""),
            notes=crypto.decrypt_text(lead.notes_encrypted),
            ref_code=refs.get(lead.conversation_id) if lead.conversation_id else None,
        )
        for lead in leads
    ]
    tenant_id = await _export_tenant(session, scope, corpus_id)
    spans = sorted({str(lead.tenant_id) for lead in leads}) if tenant_id is None else []
    # The search text is a visitor's phone or e-mail: the audit row records only that one was used.
    used = {k: ("set" if k == "q" else str(v)) for k, v in vars(filters).items() if v}
    await record_audit(
        session,
        request,
        "lead.exported",
        tenant_id=tenant_id,
        target_type="corpus",
        target_id=str(corpus_id) if corpus_id else None,
        details={
            "corpus_id": str(corpus_id) if corpus_id else None,
            "count": len(rows),
            "filters": used,
            **({"tenant_ids": spans} if len(spans) > 1 else {}),
        },
    )
    await session.commit()
    filename = f"leads-{datetime.now(UTC).date().isoformat()}.csv"
    return StreamingResponse(
        iter_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            **_NO_STORE,
        },
    )


async def _detail(session: AsyncSession, crypto: Any, lead: Lead) -> LeadDetail:
    names = await _corpus_names(session, {lead.corpus_id})
    consents = (
        (
            await session.execute(
                select(LeadConsentRow)
                .where(LeadConsentRow.lead_id == lead.id)
                .order_by(LeadConsentRow.consented_at.asc())
            )
        )
        .scalars()
        .all()
    )
    transcript: list[TranscriptMessage] = []
    if lead.conversation_id:
        # Both rows of a turn share a transaction timestamp: 'user' sorts after 'assistant',
        # so descending role puts the visitor first.
        msgs = (
            (
                await session.execute(
                    select(Message)
                    .where(Message.conversation_id == lead.conversation_id)
                    .order_by(Message.created_at.asc(), Message.role.desc())
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
        transcript = [
            TranscriptMessage(
                role=m.role,
                content=m.content,
                created_at=m.created_at,
            )
            for m in msgs
            if m.role in ("user", "assistant")
        ]
    base = _summary(lead, lead_fields(crypto, lead), names.get(lead.corpus_id, ""))
    refs = await refs_for(session, [lead.conversation_id])  # TV2 reference
    return LeadDetail(
        **base.model_dump(),
        ref_code=refs.get(lead.conversation_id) if lead.conversation_id else None,
        summary=crypto.decrypt_text(lead.summary_encrypted),
        notes=crypto.decrypt_text(lead.notes_encrypted),
        utm=lead.utm,
        callback_window=lead.callback_window,
        sources_consulted=list(lead.sources_consulted or []),
        conversation_id=lead.conversation_id,
        consents=[
            LeadConsentOut(
                notice_version=c.notice_version,
                wording_hash=c.wording_hash,
                purpose=c.purpose,
                language=c.language,
                consented_at=c.consented_at,
                withdrawn_at=c.withdrawn_at,
            )
            for c in consents
        ],
        transcript=transcript,
        retention_until=lead.retention_until,
        contacted_at=lead.contacted_at,
        updated_at=lead.updated_at,
    )


@router.get("/{lead_id}", response_model=LeadDetail)
async def get_lead_route(
    lead_id: UUID,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> LeadDetail:
    """One lead with its summary, notes, consent record and the chat it came from."""
    crypto = _crypto(request)
    lead = await _scoped_lead(request, session, lead_id)
    response.headers.update(_NO_STORE)
    return await _detail(session, crypto, lead)


@router.patch("/{lead_id}", response_model=LeadDetail)
async def update_lead_route(
    lead_id: UUID,
    body: LeadUpdateRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> LeadDetail:
    """Status, assignee, notes and qualification. Members may do this."""
    crypto = _crypto(request)
    lead = await _scoped_lead(request, session, lead_id)
    try:
        await update_lead(session, crypto, lead, body.model_dump(exclude_unset=True))
    except LeadValidationError as e:
        raise _validation_error(e) from None
    await session.commit()
    await session.refresh(lead)
    response.headers.update(_NO_STORE)
    return await _detail(session, crypto, lead)


@router.delete("/{lead_id}", status_code=204)
async def delete_lead_route(
    lead_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _owner: None = Depends(require_owner),
) -> None:
    """Erase a lead and the widget chat it came from. Owners only; audited."""
    lead = await _scoped_lead(request, session, lead_id)
    await record_audit(
        session,
        request,
        "lead.deleted",
        tenant_id=lead.tenant_id,
        target_type="lead",
        target_id=str(lead.id),
        details={"corpus_id": str(lead.corpus_id), "lead_id": str(lead.id)},
    )
    await erase_lead(session, lead, withdrawn=False)
    await session.commit()
