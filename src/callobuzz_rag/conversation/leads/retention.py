"""Daily purge: leads past their retention date, and consent records a year after their
lead is gone (design section 7; the DPDP rules keep such logs one year).

The year runs from `lead_consents.erased_at`, stamped when the lead goes (here, in an owner's
delete, in a withdrawal), not from the day of consent: a lead kept two years must not lose its
proof of consent the night it is purged. A record whose lead vanished some other way (a deleted
tenant cascades) has no `erased_at` and falls back to the consent date.

Also clears a conversational capture (a phone or e-mail held sealed in the chat state while the
visitor had not yet agreed) from chats idle for a day: contact data never consented to must not
outlive the conversation.

A lead's widget chat goes with it (it holds what the visitor typed), exactly as in an owner's
delete or a withdrawal; chats from the dashboard or the API are never touched. So does the
customer's WhatsApp chat (`channels/whatsapp/erasure.py`).
"""

import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, literal_column, or_, select, text, update

from callobuzz_rag.accounts.audit import record_audit
from callobuzz_rag.db.models.assistant_turn import AssistantTurn
from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.handoff import Handoff
from callobuzz_rag.db.models.lead import Lead
from callobuzz_rag.db.models.lead_consent import LeadConsent as LeadConsentRow

_CONSENT_KEEP = timedelta(days=365)
_CAPTURE_KEEP = timedelta(days=1)

# Window = the published config's `leads.consent.retention_days`, 365 with none published.
_AGED_HANDOFFS = """
UPDATE handoffs h SET summary_encrypted = NULL, page_url = NULL
WHERE (h.summary_encrypted IS NOT NULL OR h.page_url IS NOT NULL)
  AND h.created_at < CAST(:now AS timestamptz) - make_interval(days => COALESCE(
      (SELECT (ac.config->'leads'->'consent'->>'retention_days')::int
         FROM assistant_configs ac
        WHERE ac.corpus_id = h.corpus_id AND ac.status = 'published'),
      :default_days))
"""


async def purge_expired_leads(session_factory: Any, now: datetime | None = None) -> dict[str, int]:
    moment = now or datetime.now(UTC)
    async with session_factory() as session:
        # A handoff's summary names the visitor: scrub it with its lead (the delete only NULLs
        # the links), and from every handoff past the knowledge base's retention window, lead or
        # not (DELTA-C5 D5). The rows stay as counts.
        expired = select(Lead.id).where(Lead.retention_until < moment)
        expired_chats = select(Lead.conversation_id).where(
            Lead.retention_until < moment, Lead.conversation_id.is_not(None)
        )
        await session.execute(
            update(Handoff)
            .where(or_(Handoff.lead_id.in_(expired), Handoff.conversation_id.in_(expired_chats)))
            .values(summary_encrypted=None)
        )
        await session.execute(text(_AGED_HANDOFFS).bindparams(now=moment, default_days=365))
        # Stamp the consent rows before their leads go (the delete sets `lead_id` NULL).
        await session.execute(
            update(LeadConsentRow)
            .where(LeadConsentRow.lead_id.in_(select(Lead.id).where(Lead.retention_until < moment)))
            .values(erased_at=moment)
        )
        deleted = await session.execute(
            delete(Lead)
            .where(Lead.retention_until < moment)
            .returning(Lead.corpus_id, Lead.tenant_id, Lead.conversation_id, Lead.phone_hash)
        )
        rows = deleted.all()
        per_corpus = Counter((str(c), str(t)) for c, t, _, _ in rows)
        conversation_ids = [cv for _, _, cv, _ in rows if cv is not None]
        # TV6: the WhatsApp chats of these leads (number, name, every window, stored events)
        # go with them, in this transaction and before the conversation links are removed.
        from callobuzz_rag.channels.whatsapp.erasure import erase_chats

        await erase_chats(session, [(c, cv, ph) for c, _, cv, ph in rows])
        if conversation_ids:
            # the questions leave the frozen value reports with the turns (TV4)
            from callobuzz_rag.business.value_privacy import delete_assistant_turns

            await delete_assistant_turns(
                session, AssistantTurn.conversation_id.in_(_widget_chats(conversation_ids))
            )
            await session.execute(
                delete(Conversation).where(
                    Conversation.id.in_(conversation_ids),
                    Conversation.channel.in_(("widget", "whatsapp")),  # TV6 channel
                )
            )
        consents = await session.execute(
            delete(LeadConsentRow).where(
                LeadConsentRow.lead_id.is_(None),
                func.coalesce(LeadConsentRow.erased_at, LeadConsentRow.consented_at)
                < moment - _CONSENT_KEEP,
            )
        )
        await session.execute(
            update(LeadConsentRow)
            .where(
                LeadConsentRow.consented_at < moment - _CONSENT_KEEP,
                LeadConsentRow.ip.is_not(None),
            )
            .values(ip=None)
        )
        captures = await session.execute(
            update(Conversation)
            .where(
                func.jsonb_typeof(Conversation.assistant_state["lead_capture"]) == "object",
                Conversation.updated_at < moment - _CAPTURE_KEEP,
            )
            .values(
                assistant_state=func.jsonb_set(
                    Conversation.assistant_state,
                    literal_column("'{lead_capture}'"),
                    literal_column("'null'::jsonb"),
                ),
                updated_at=Conversation.updated_at,
            )
        )
        for (corpus_id, tenant_id), count in per_corpus.items():
            await record_audit(
                session,
                None,
                "lead.retention_purged",
                tenant_id=uuid.UUID(tenant_id),
                target_type="corpus",
                target_id=corpus_id,
                details={"corpus_id": corpus_id, "count": count},
                actor=("system", None, "lead retention"),
            )
        await session.commit()
    return {
        "leads": sum(per_corpus.values()),
        "consents": int(consents.rowcount or 0),
        "captures": int(captures.rowcount or 0),
    }


def _widget_chats(conversation_ids: list[uuid.UUID]) -> Any:
    return select(Conversation.id).where(
        Conversation.id.in_(conversation_ids),
        Conversation.channel.in_(("widget", "whatsapp")),  # TV6 channel
    )
