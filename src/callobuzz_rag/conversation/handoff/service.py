"""Recording handoffs and shaping what the business receives (design 7)."""

import uuid
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.email.render import dashboard_link
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.handoffs import CallbackWindow
from callobuzz_rag.conversation.handoff.triggers import reason_label
from callobuzz_rag.conversation.schema import BusinessCard
from callobuzz_rag.conversation.types import OfficeState
from callobuzz_rag.db.models.handoff import Handoff

_MAX_DAYS_AHEAD = 14


def validate_callback_window(window: CallbackWindow, card: BusinessCard, now: datetime) -> None:
    """The window is in the business's time zone. It is not checked against opening hours:
    a callback may be asked for any time in the next 14 days."""
    tz = ZoneInfo(card.timezone)
    start = datetime.combine(window.date, datetime.strptime(window.start, "%H:%M").time(), tz)
    end = datetime.combine(window.date, datetime.strptime(window.end, "%H:%M").time(), tz)
    today = now.astimezone(tz).date()
    problem = None
    if end <= start:
        problem = "The end time must be after the start time."
    elif end <= now:
        problem = "That time has already passed. Pick a later time."
    elif window.date > today + timedelta(days=_MAX_DAYS_AHEAD):
        problem = f"Pick a day within the next {_MAX_DAYS_AHEAD} days."
    if problem:
        raise APIError(422, problem, "callback_window_invalid")


async def create_handoff(
    session: AsyncSession,
    *,
    corpus_id: uuid.UUID,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    channel: str,
    reason: str,
    language: str | None,
    page_url: str | None,
    office: OfficeState,
    lead_id: uuid.UUID | None = None,
) -> Handoff:
    """Add and flush a handoff row; the caller commits (with whatever else it changed).
    `created_at` is filled by the database: reload the row before building a payload."""
    row = Handoff(
        corpus_id=corpus_id,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        lead_id=lead_id,
        channel=channel,
        reason=reason,
        language=language,
        page_url=page_url[:2048] if page_url else None,
        office_open=None if office is OfficeState.UNKNOWN else office is OfficeState.OPEN,
        status="new",
    )
    session.add(row)
    await session.flush()
    return row


def transcript_url(dashboard_url: str, corpus_id: uuid.UUID, handoff_id: uuid.UUID) -> str:
    return dashboard_link(dashboard_url, f"leads?kb={corpus_id}&tab=handoffs&handoff={handoff_id}")


def handoff_payload(
    handoff: Handoff,
    *,
    summary: str,
    question: str,
    visitor: dict[str, str | None] | None,
    transcript_url: str,
    sources: list[str],
    ref_code: str | None = None,  # TV2 reference
) -> dict[str, Any]:
    """The `handoff.requested` webhook body's `data` (also the e-mail's facts)."""
    return {
        "handoff_id": str(handoff.id),
        "corpus_id": str(handoff.corpus_id),
        "tenant_id": str(handoff.tenant_id),
        "conversation_id": str(handoff.conversation_id) if handoff.conversation_id else None,
        "lead_id": str(handoff.lead_id) if handoff.lead_id else None,
        "created_at": handoff.created_at.isoformat(),
        "channel": handoff.channel,
        "reason": handoff.reason,
        "reason_label": reason_label(handoff.reason),
        "office_open": handoff.office_open,
        "language": handoff.language,
        "page_url": handoff.page_url,
        "summary": summary,
        "question": question,
        "visitor": visitor or {"name": None, "phone": None, "email": None},
        "transcript_url": transcript_url,
        "sources": sources,
        "ref_code": ref_code,  # TV2 reference
    }
