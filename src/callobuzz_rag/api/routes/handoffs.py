"""Handoffs (conversation layer C5): who asked for a person, how, and why.

Scoped like every list: admin all knowledge bases, a tenant its own, a corpus key its one. A
handoff outside the caller's scope is a 404 (`handoff_not_found`), so ids cannot be probed.
Members may read and update (staff follow handoffs up); nothing here is owner-only.

The list carries no contact data. The detail may (a callback request's lead), so it is a
`write` operation for named keys and is never cached.
"""

import logging
from datetime import UTC, date, datetime, time, timedelta
from typing import cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import authorized_corpus_ids, get_session, resolve_scope
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.handoffs import (
    HandoffDetailResponse,
    HandoffLead,
    HandoffListResponse,
    HandoffResponse,
    HandoffStatus,
    HandoffTranscriptMessage,
    ReferenceLookupResponse,  # TV2 reference
    ReferenceMatch,  # TV2 reference
    UpdateHandoffRequest,
)
from callobuzz_rag.conversation.handoff.reference import (  # TV2 reference
    find_matches,
    normalise_ref,
    refs_for,
)
from callobuzz_rag.conversation.handoff.triggers import reason_label
from callobuzz_rag.core.secrets import EncryptionError, encryptor_from_settings
from callobuzz_rag.db.models.handoff import Handoff
from callobuzz_rag.db.models.message import Message

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/handoffs", tags=["handoffs"])

_NO_STORE = {"Cache-Control": "no-store"}
_TRANSCRIPT_LIMIT = 200


def _out(row: Handoff, ref_code: str | None = None) -> HandoffResponse:  # TV2 reference
    return HandoffResponse(
        id=row.id,
        corpus_id=row.corpus_id,
        conversation_id=row.conversation_id,
        lead_id=row.lead_id,
        channel=row.channel,
        reason=row.reason,
        reason_label=reason_label(row.reason),
        language=row.language,
        page_url=row.page_url,
        office_open=row.office_open,
        status=cast("HandoffStatus", row.status),
        handled_at=row.handled_at,
        notified_at=row.notified_at,
        created_at=row.created_at,
        ref_code=ref_code,  # TV2 reference
    )


async def _load(request: Request, session: AsyncSession, handoff_id: UUID) -> Handoff:
    allowed = await authorized_corpus_ids(resolve_scope(request), None, session)
    row = await session.get(Handoff, handoff_id)
    if row is None or (allowed is not None and row.corpus_id not in allowed):
        raise APIError(404, "Handoff not found", "handoff_not_found")
    return row


@router.get("", response_model=HandoffListResponse)
async def list_handoffs(
    request: Request,
    corpus_id: UUID | None = Query(None),
    status: HandoffStatus | None = Query(None),
    reason: str | None = Query(None, max_length=32),
    channel: str | None = Query(None, max_length=20),
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to", description="Inclusive"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> HandoffListResponse:
    """The handoffs of the knowledge bases the caller may see, newest first."""
    allowed = await authorized_corpus_ids(resolve_scope(request), corpus_id, session)
    where: list[ColumnElement[bool]] = []
    if allowed is not None:
        where.append(Handoff.corpus_id.in_(list(allowed)))
    if status:
        where.append(Handoff.status == status)
    if reason:
        where.append(Handoff.reason == reason)
    if channel:
        where.append(Handoff.channel == channel)
    if date_from:
        where.append(Handoff.created_at >= datetime.combine(date_from, time.min, UTC))
    if date_to:
        where.append(
            Handoff.created_at < datetime.combine(date_to + timedelta(days=1), time.min, UTC)
        )
    rows = await session.execute(
        select(Handoff)
        .where(*where)
        .order_by(Handoff.created_at.desc(), Handoff.id)
        .offset(offset)
        .limit(limit)
    )
    page = list(rows.scalars().all())
    refs = await refs_for(session, [r.conversation_id for r in page])  # TV2 reference
    items = [_out(r, refs.get(r.conversation_id) if r.conversation_id else None) for r in page]
    total = (await session.execute(select(func.count(Handoff.id)).where(*where))).scalar() or 0
    return HandoffListResponse(items=items, total=int(total), offset=offset, limit=limit)


# TV2 reference: declared before `/{handoff_id}` so "lookup" is never read as an id
@router.get("/lookup", response_model=ReferenceLookupResponse)
async def lookup_reference(
    request: Request,
    ref: str = Query(..., max_length=40, description="A reference, e.g. SD-4F7K (any case)"),
    session: AsyncSession = Depends(get_session),
) -> ReferenceLookupResponse:
    """Find the conversation a visitor quoted a reference for. Ids only: the drawer then loads
    the detail. Only knowledge bases the caller may read are searched."""
    code = normalise_ref(ref)
    if code is None:
        raise APIError(
            422,
            "That doesn't look like a reference. Type it like SD-4F7K.",
            "reference_invalid",
        )
    allowed = await authorized_corpus_ids(resolve_scope(request), None, session)
    matches = await find_matches(session, code, allowed)
    if not matches:
        raise APIError(
            404,
            "No conversation with that reference in your knowledge bases.",
            "reference_not_found",
        )
    return ReferenceLookupResponse(
        ref=code, matches=[ReferenceMatch.model_validate(m) for m in matches]
    )


# /TV2 reference


async def _linked_lead(request: Request, session: AsyncSession, row: Handoff) -> HandoffLead | None:
    """The callback request's lead: name, phone, e-mail. None when there is none, it was
    erased or the key that sealed it is not configured."""
    if row.lead_id is None:
        return None
    from callobuzz_rag.conversation.leads.crypto import (
        LeadCryptoUnavailableError,
        lead_crypto_from_settings,
    )
    from callobuzz_rag.conversation.leads.service import lead_fields
    from callobuzz_rag.db.models.lead import Lead

    lead = await session.get(Lead, row.lead_id)
    if lead is None:
        return None
    try:
        fields = lead_fields(lead_crypto_from_settings(request.app.state.settings), lead)
    except (LeadCryptoUnavailableError, EncryptionError, ValueError):
        return None
    return HandoffLead(
        id=lead.id, name=fields.get("name"), phone=fields.get("phone"), email=fields.get("email")
    )


@router.get("/{handoff_id}", response_model=HandoffDetailResponse)
async def get_handoff(
    handoff_id: UUID,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> HandoffDetailResponse:
    """One handoff with its summary, the masked chat and, for a callback, the lead."""
    row = await _load(request, session, handoff_id)
    transcript: list[HandoffTranscriptMessage] = []
    if row.conversation_id is not None:
        msgs = await session.execute(
            select(Message)
            .where(Message.conversation_id == row.conversation_id)
            .order_by(Message.created_at.asc(), Message.role.desc())
            .limit(_TRANSCRIPT_LIMIT)
        )
        transcript = [
            HandoffTranscriptMessage(role=m.role, content=m.content, created_at=m.created_at)
            for m in msgs.scalars().all()
        ]
    summary: str | None = None
    if row.summary_encrypted:
        try:
            encryptor = encryptor_from_settings(request.app.state.settings)
            summary = encryptor.decrypt(row.summary_encrypted) if encryptor else None
        except EncryptionError:
            logger.warning("Handoff %s: its summary could not be decrypted", row.id)
    lead = await _linked_lead(request, session, row)
    response.headers.update(_NO_STORE)
    refs = await refs_for(session, [row.conversation_id])  # TV2 reference
    return HandoffDetailResponse(
        **_out(row, refs.get(row.conversation_id) if row.conversation_id else None).model_dump(),
        summary=summary,
        transcript=transcript,
        lead=lead,
    )


@router.patch("/{handoff_id}", response_model=HandoffResponse)
async def update_handoff(
    handoff_id: UUID,
    body: UpdateHandoffRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HandoffResponse:
    """Mark a handoff handled (or new again)."""
    row = await _load(request, session, handoff_id)
    row.status = body.status
    row.handled_at = datetime.now(UTC) if body.status == "handled" else None
    await session.commit()
    refs = await refs_for(session, [row.conversation_id])  # TV2 reference
    return _out(row, refs.get(row.conversation_id) if row.conversation_id else None)
