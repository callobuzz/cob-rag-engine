"""Pages into documents (BIZ1): the normal records pipeline, ten pages at a time.

`ingest_records` is all-or-nothing per batch (it checks the document room for the whole batch),
so a batch is small and the room under the plan's document limit is computed before each one;
pages beyond it are reported by the caller as `over_limit`. One `IngestJob` per batch with
`mode="website"` makes the work visible in the knowledge base's activity like any import.
"""

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import select

from callobuzz_rag.db.models.document import Document
from callobuzz_rag.db.models.ingest_job import IngestJob
from callobuzz_rag.pipeline.records import TextRecord
from callobuzz_rag.website.urls import external_id

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.db.models.corpus import Corpus

BATCH_SIZE: Final = 10
MODE: Final = "website"

# (batch, corpus id, job id) -> the pipeline's outcome dict ({"status": "completed", ...})
IngestFn = Callable[[list[TextRecord], uuid.UUID, uuid.UUID], Awaitable[dict[str, Any]]]


@dataclass
class BatchOutcome:
    ok: bool
    error: str | None
    documents: dict[str, uuid.UUID]  # record id -> document id, for the records that exist now


def page_record(
    url: str, title: str, markdown: str, scope: str, *, fallback_title: str | None = None
) -> TextRecord:
    """The record of one page. Metadata holds STABLE fields only (D3): anything in it is part of
    the fingerprint, and a fingerprint that changed on every visit would re-embed every page."""
    return TextRecord(
        id=external_id(url),
        title=(title or fallback_title or url)[:500],
        text=markdown,
        url=url,
        metadata={"source": "website", "site": scope},
        markdown=True,
    )


async def document_room(session: "AsyncSession", corpus: "Corpus") -> int | None:
    """How many NEW documents the workspace can still add (None = no limit): the effective
    limit (plan, overrides, add-ons) minus active and queued documents."""
    from callobuzz_rag.accounts import caps

    limit = (await caps.effective_limits(session, corpus.tenant_id)).max_documents
    if limit is None:
        return None
    used, queued = await caps.document_usage(session, corpus.tenant_id)
    return max(limit - used - queued, 0)


async def known_documents(
    session: "AsyncSession", corpus_id: uuid.UUID, record_ids: Sequence[str]
) -> dict[str, uuid.UUID]:
    """Record id -> document id for the records that are already documents of the corpus."""
    if not record_ids:
        return {}
    rows = await session.execute(
        select(Document.external_id, Document.id).where(
            Document.corpus_id == corpus_id, Document.external_id.in_(list(record_ids))
        )
    )
    return {str(ext): doc_id for ext, doc_id in rows.all() if ext}


async def ingest_batch(
    session_factory: Callable[[], Any],
    ingest: IngestFn,
    corpus_id: uuid.UUID,
    records: list[TextRecord],
) -> BatchOutcome:
    """Queue one job for `records`, run the pipeline on them and read back their documents."""
    ids = [r.id for r in records]
    async with session_factory() as session:
        known = await known_documents(session, corpus_id, ids)
        job = IngestJob(
            status="queued",
            total_documents=len(records),
            corpus_id=corpus_id,
            mode=MODE,
            pending_new_documents=sum(1 for i in ids if i not in known),
        )
        session.add(job)
        await session.commit()
        job_id = job.id
    result = await ingest(records, corpus_id, job_id)
    ok = result.get("status") == "completed"
    async with session_factory() as session:
        documents = await known_documents(session, corpus_id, ids) if ok else {}
    error = None if ok else str(result.get("error") or result.get("failed_at_step") or "failed")
    return BatchOutcome(ok=ok, error=error, documents=documents)
