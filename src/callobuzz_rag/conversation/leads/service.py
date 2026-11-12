"""Lead persistence. Contact values are encrypted on the way in and decrypted only for
an authorised reader (the inbox routes, the notifier, the export)."""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import ColumnElement, delete, func, or_, select, update

from callobuzz_rag.conversation.leads.consent import consent_row
from callobuzz_rag.conversation.leads.validation import (
    CleanLead,
    LeadValidationError,
    normalize_email,
    normalize_phone,
)
from callobuzz_rag.conversation.qualification import qualify
from callobuzz_rag.db.models.assistant_turn import AssistantTurn
from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.handoff import Handoff
from callobuzz_rag.db.models.lead import LEAD_QUALIFICATIONS, LEAD_STATUSES, Lead
from callobuzz_rag.db.models.lead_consent import LeadConsent as LeadConsentRow
from callobuzz_rag.db.models.tenant_member import TenantMember

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.conversation.leads.crypto import LeadCrypto
    from callobuzz_rag.conversation.schema import AssistantConfig


@dataclass
class NewLead:
    corpus_id: uuid.UUID
    tenant_id: uuid.UUID
    conversation_id: uuid.UUID | None
    widget_key_id: uuid.UUID | None
    channel: str
    clean: CleanLead
    language: str
    page_url: str | None
    utm: dict[str, str] | None
    trigger: str
    source_label: str | None
    tags: list[str]
    callback_window: dict[str, Any] | None
    sources_consulted: list[str]
    notice_text: str
    ip: str | None
    # sha256 of the secret in the visitor's withdrawal link (`leads/tokens.py`); None when no
    # link is shown (a lead captured in the chat).
    withdraw_hash: str | None = None


@dataclass
class LeadFilters:
    corpus_id: uuid.UUID | None = None
    status: str | None = None
    trigger: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    q: str | None = None
    page_url: str | None = None
    tags: list[str] = field(default_factory=list)
    qualification: str | None = None


async def create_lead(
    session: "AsyncSession",
    crypto: "LeadCrypto",
    config: "AssistantConfig",
    new: NewLead,
    now: datetime | None = None,
) -> Lead:
    """Add the lead and its consent row and flush. The caller commits.

    `retention_until` comes from the config the visitor saw (the one passed in).
    """
    moment = now or datetime.now(UTC)
    lead = Lead(
        id=uuid.uuid4(),
        corpus_id=new.corpus_id,
        tenant_id=new.tenant_id,
        conversation_id=new.conversation_id,
        widget_key_id=new.widget_key_id,
        channel=new.channel,
        fields_encrypted=crypto.encrypt_json(new.clean.as_payload()),
        phone_hash=crypto.index_hash(new.clean.phone),
        email_hash=crypto.index_hash(new.clean.email.lower() if new.clean.email else None),
        language=new.language,
        page_url=new.page_url[:2048] if new.page_url else None,
        utm=new.utm,
        trigger=new.trigger,
        source_label=new.source_label,
        tags=list(new.tags),
        callback_window=new.callback_window,
        status="new",
        qualification=qualify(new.clean.as_payload(), new.trigger, config.leads.qualification),
        sources_consulted=new.sources_consulted[:20],
        withdraw_hash=new.withdraw_hash,
        retention_until=moment + timedelta(days=config.leads.consent.retention_days),
        created_at=moment,
        updated_at=moment,
    )
    session.add(lead)
    session.add(
        consent_row(
            lead_id=lead.id,
            corpus_id=new.corpus_id,
            config=config,
            language=new.language,
            notice_text=new.notice_text,
            ip=new.ip,
        )
    )
    await session.flush()
    # TV2 reference: a lead left in a conversation gives it its reference (or reuses it), so
    # the owner and the visitor name the same chat
    if new.conversation_id is not None:
        from callobuzz_rag.conversation.handoff.reference import assign_ref, ref_prefix

        try:
            # a SAVEPOINT: a database error rolls back only the reference, never the lead
            async with session.begin_nested():
                await assign_ref(
                    session, new.conversation_id, ref_prefix(config.identity.business_name or "")
                )
        except Exception:
            logger.warning("Could not give a lead's conversation a reference", exc_info=True)
    # /TV2 reference
    return lead


def lead_fields(crypto: "LeadCrypto", lead: Lead) -> dict[str, Any]:
    """The decrypted contact data. A missing key reads as None / empty."""
    data = crypto.decrypt_json(lead.fields_encrypted)
    return {
        "name": data.get("name"),
        "phone": data.get("phone"),
        "email": data.get("email"),
        "custom": data.get("custom") or {},
    }


def _contact_hashes(crypto: "LeadCrypto", q: str) -> tuple[str, list[str]]:
    """Which hash column to search and every hash the value may carry (one per key)."""
    if "@" in q:
        try:
            return "email", crypto.index_hashes(normalize_email(q).lower())
        except LeadValidationError:
            return "email", []
    try:
        return "phone", crypto.index_hashes(normalize_phone(q))
    except LeadValidationError:
        return "phone", []


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _filter_clauses(crypto: "LeadCrypto", f: LeadFilters) -> list[ColumnElement[bool]]:
    clauses: list[ColumnElement[bool]] = []
    if f.corpus_id is not None:
        clauses.append(Lead.corpus_id == f.corpus_id)
    if f.status:
        clauses.append(Lead.status == f.status)
    if f.trigger:
        clauses.append(Lead.trigger == f.trigger)
    if f.date_from:
        clauses.append(Lead.created_at >= datetime.combine(f.date_from, datetime.min.time(), UTC))
    if f.date_to:
        end = datetime.combine(f.date_to, datetime.min.time(), UTC) + timedelta(days=1)
        clauses.append(Lead.created_at < end)
    if f.page_url:
        clauses.append(Lead.page_url.ilike(f"%{_escape_like(f.page_url[:200])}%", escape="\\"))
    if f.qualification:
        clauses.append(Lead.qualification == f.qualification)
    if f.tags:
        clauses.append(Lead.tags.contains(f.tags))
    if f.q:
        kind, digests = _contact_hashes(crypto, f.q)
        column = Lead.email_hash if kind == "email" else Lead.phone_hash
        clauses.append(column.in_(digests or ["-"]))
    return clauses


async def list_leads(
    session: "AsyncSession",
    *,
    corpus_ids: Sequence[uuid.UUID] | None,
    filters: LeadFilters,
    crypto: "LeadCrypto",
    offset: int,
    limit: int,
) -> tuple[list[Lead], int]:
    clauses = _filter_clauses(crypto, filters)
    if corpus_ids is not None:
        clauses.append(Lead.corpus_id.in_(list(corpus_ids)))
    rows = await session.execute(
        select(Lead).where(*clauses).order_by(Lead.created_at.desc()).offset(offset).limit(limit)
    )
    total = await session.execute(select(func.count(Lead.id)).where(*clauses))
    return list(rows.scalars().all()), int(total.scalar() or 0)


async def update_lead(
    session: "AsyncSession",
    crypto: "LeadCrypto",
    lead: Lead,
    changes: dict[str, Any],
    now: datetime | None = None,
) -> Lead:
    moment = now or datetime.now(UTC)
    if "status" in changes:
        status = changes["status"]
        if status not in LEAD_STATUSES:
            raise LeadValidationError("lead_field_invalid", field="status", reason="not_an_option")
        if status == "contacted" and lead.contacted_at is None:
            lead.contacted_at = moment
        lead.status = status
    if "qualification" in changes:
        value = changes["qualification"]
        if value is not None and value not in LEAD_QUALIFICATIONS:
            raise LeadValidationError(
                "lead_field_invalid", field="qualification", reason="not_an_option"
            )
        lead.qualification = value
    if "notes" in changes:
        notes = changes["notes"]
        lead.notes_encrypted = crypto.encrypt_text(notes) if notes else None
    if "assignee_user_id" in changes:
        user_id = changes["assignee_user_id"]
        if user_id is not None:
            member = await session.execute(
                select(TenantMember).where(
                    TenantMember.tenant_id == lead.tenant_id, TenantMember.user_id == user_id
                )
            )
            if member.scalar_one_or_none() is None:
                raise LeadValidationError(
                    "lead_field_invalid", field="assignee_user_id", reason="not_member"
                )
        lead.assignee_user_id = user_id
    lead.updated_at = moment
    return lead


async def erase_lead(
    session: "AsyncSession", lead: Lead, *, withdrawn: bool, now: datetime | None = None
) -> None:
    """Delete the lead, and with it the visitor's widget chat.

    Consent rows stay (their `lead_id` goes NULL) as the record of what was agreed, stamped
    `erased_at` so the retention task keeps them a year from now; with `withdrawn` they also
    get `withdrawn_at`. The widget transcript is masked but still
    holds what the visitor typed, so it goes too: its assistant turns first (their
    conversation link is SET NULL, they would outlive it), then the conversation (its
    messages cascade). Conversations from the dashboard or the API are never touched.
    """
    moment = now or datetime.now(UTC)
    stamps: dict[str, datetime] = {"erased_at": moment}
    if withdrawn:
        stamps["withdrawn_at"] = moment
    await session.execute(
        update(LeadConsentRow).where(LeadConsentRow.lead_id == lead.id).values(**stamps)
    )
    conversation_id = lead.conversation_id
    # TV6: the customer's WhatsApp chat (number, name, every window's conversation, the stored
    # events) goes first, while the links that find it still exist; same transaction.
    from callobuzz_rag.channels.whatsapp.erasure import erase_chats

    await erase_chats(session, [(lead.corpus_id, conversation_id, lead.phone_hash)])
    # The handoff row stays (a count for the owner) but the visitor's summary does not: its
    # links go NULL with the lead and the chat, so it would outlive both (DELTA-C5 D5).
    linked = Handoff.lead_id == lead.id
    if conversation_id is not None:
        linked = or_(linked, Handoff.conversation_id == conversation_id)
    await session.execute(update(Handoff).where(linked).values(summary_encrypted=None))
    await session.delete(lead)
    if conversation_id is not None:
        widget_chat = select(Conversation.id).where(
            Conversation.id == conversation_id,
            Conversation.channel.in_(("widget", "whatsapp")),  # TV6 channel
        )
        # the questions leave the frozen value reports with the turns (TV4)
        from callobuzz_rag.business.value_privacy import delete_assistant_turns

        await delete_assistant_turns(session, AssistantTurn.conversation_id.in_(widget_chat))
        await session.execute(
            delete(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.channel.in_(("widget", "whatsapp")),  # TV6 channel
            )
        )
