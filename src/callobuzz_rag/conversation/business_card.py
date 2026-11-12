"""The business card as a small system document (design 4.1), so retrieval and citations
treat card facts like any document.

Record id `system:business-card`. Customer imports may not use the `system:` prefix, system
documents do not count against a plan's documents (`accounts/caps.py`), the worker counts none
of them toward the document cap, and boilerplate stripping never touches them.

The card is indexed only while the assistant is on and the card has facts. When it is emptied,
or the assistant is switched off, the card document is deleted (no placeholder text is indexed:
that would be a false statement), so a removed phone number stops being searchable and
`assistant.enabled=false` answers exactly as before.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from callobuzz_rag.conversation.compiler import card_lines, card_today
from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.db.models.document import Document
from callobuzz_rag.db.models.document_version import DocumentVersion
from callobuzz_rag.db.models.ingest_job import IngestJob
from callobuzz_rag.pipeline.records import SYSTEM_RECORD_PREFIX, TextRecord

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

BUSINESS_CARD_RECORD_ID = f"{SYSTEM_RECORD_PREFIX}business-card"
CARD_TITLE = "Business card"


@dataclass(frozen=True)
class PendingCard:
    """A card import (job_id + record) or removal (`remove`) the caller performs AFTER it
    commits."""

    job_id: uuid.UUID | None = None
    record: TextRecord | None = None
    remove: bool = False


def card_markdown(config: AssistantConfig, today: date | None = None) -> str | None:
    """The card as text, or None when it holds no facts (a name alone is not a card)."""
    lines = card_lines(config, today or card_today(config))
    if not lines:
        return None
    name = config.identity.business_name or "Our business"
    body = "\n".join(f"- {line}" for line in lines)
    return f"{name} - business card\n\n{body}\n"


def effective_card_text(config: AssistantConfig) -> str | None:
    """What should be searchable: nothing while the assistant is switched off."""
    return card_markdown(config) if config.enabled else None


def _record(text: str) -> TextRecord:
    return TextRecord(
        id=BUSINESS_CARD_RECORD_ID,
        text=text,
        title=CARD_TITLE,
        metadata={"system": "business_card"},
    )


def card_record(config: AssistantConfig) -> TextRecord | None:
    text = card_markdown(config)
    return None if text is None else _record(text)


async def _card_document_exists(session: "AsyncSession", corpus_id: uuid.UUID) -> bool:
    result = await session.execute(
        select(Document.id)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(
            Document.corpus_id == corpus_id,
            Document.external_id == BUSINESS_CARD_RECORD_ID,
            DocumentVersion.status == "active",
        )
        .limit(1)
    )
    return result.first() is not None


async def sync_business_card(
    session: "AsyncSession",
    corpus: Any,
    config: AssistantConfig,
    previous: AssistantConfig | None,
) -> PendingCard | None:
    """Queue a records job for the card when what is searchable must change.

    That is: the effective text differs from the previous config's, this is the first sync, or
    the document is missing (a failed job, a worker outage at publish time or a deleted
    document never heals otherwise). An emptied or switched-off card is removed.
    The caller commits, then dispatches the returned `PendingCard`.
    """
    new_text = effective_card_text(config)
    old_text = effective_card_text(previous) if previous is not None else None
    if new_text is not None:
        unchanged = previous is not None and old_text == new_text
        if unchanged and await _card_document_exists(session, corpus.id):
            return None
        record = _record(new_text)
    elif old_text is not None:
        return PendingCard(remove=True)
    else:
        return None
    job = IngestJob(
        id=uuid.uuid4(),
        status="queued",
        total_documents=1,
        corpus_id=corpus.id,
        mode="records",
        pending_new_documents=0,
    )
    session.add(job)
    return PendingCard(job_id=job.id, record=record)


async def remove_business_card(session: "AsyncSession", vector_store: Any, corpus: Any) -> None:
    """Delete the card document (vectors first, then the rows) and commit."""
    from callobuzz_rag.api.services.document_removal import remove_document

    doc = (
        await session.execute(
            select(Document).where(
                Document.corpus_id == corpus.id,
                Document.external_id == BUSINESS_CARD_RECORD_ID,
            )
        )
    ).scalar_one_or_none()
    if doc is None:
        return
    await remove_document(session, vector_store, corpus, doc)
    await session.commit()


async def dispatch_business_card(
    session: "AsyncSession", corpus: Any, pending: PendingCard, vector_store: Any = None
) -> None:
    """Hand the import to the worker (a queue that refuses it fails the job, never `queued`),
    or delete the card document for a removal."""
    if pending.remove:
        if vector_store is not None:
            await remove_business_card(session, vector_store, corpus)
        return
    if pending.record is None or pending.job_id is None:
        return
    from callobuzz_rag.worker.tasks import ingest_records_task

    record = pending.record
    try:
        ingest_records_task.delay(
            records=[
                {
                    "id": record.id,
                    "text": record.text,
                    "title": record.title,
                    "url": record.url,
                    "metadata": record.metadata,
                }
            ],
            corpus_id=str(corpus.id),
            job_id=str(pending.job_id),
            qdrant_collection=corpus.qdrant_collection,
        )
    except Exception as exc:
        from callobuzz_rag.api.routes.ingest import _fail_undispatched

        logger.error("Business card import for %s was not queued", corpus.id, exc_info=True)
        job = await session.get(IngestJob, pending.job_id)
        if job is not None:
            await _fail_undispatched(session, job, exc)
