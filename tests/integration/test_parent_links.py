"""Parent links against real PostgreSQL: ingest stores them, the backfill recovers them.

Ingest used to link children to parent ids the database had not assigned yet, so no
chunk was ever stored with a parent. The self-referencing foreign key is only checked
by a real database. Requires Docker.
"""

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update

from callobuzz_rag.chunking.chunker import ChunkerConfig, StructureAwareChunker
from callobuzz_rag.db.models.chunk import Chunk
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.embeddings.base import EmbeddingResult
from callobuzz_rag.enrichment.extractive import ExtractiveEnrichmentProvider
from callobuzz_rag.parsing.models import ElementType, ParsedDocument, ParsedElement
from callobuzz_rag.pipeline.models import IngestMode, JobStatus
from callobuzz_rag.pipeline.orchestrator import IngestionOrchestrator
from callobuzz_rag.pipeline.records import TextRecord
from callobuzz_rag.retrieval.reindex import backfill_parent_links
from tests.integration.conftest import new_tenant_id

pytestmark = pytest.mark.integration

SMALL = ChunkerConfig(target_tokens=30, min_tokens=5, max_tokens=60, overlap_tokens=0)
PARAGRAPHS = [
    f"Paragraph {i} explains rule {i} of the returns policy in plain words, "
    f"with enough detail that it needs a chunk of its own, number {i}."
    for i in range(3)
]


def _orchestrator(session) -> IngestionOrchestrator:
    parser = AsyncMock()
    parser.parse.return_value = ParsedDocument(
        elements=[
            ParsedElement(type=ElementType.HEADING, text="Returns", level=1),
            *[ParsedElement(type=ElementType.PARAGRAPH, text=p) for p in PARAGRAPHS],
            ParsedElement(type=ElementType.HEADING, text="Shipping", level=1),
            ParsedElement(type=ElementType.PARAGRAPH, text="Orders ship in two days."),
        ],
        source_filename="policy.md",
        parser_name="mock",
    )
    embedder = AsyncMock()
    embedder.embed_texts.side_effect = lambda texts, **kw: [
        EmbeddingResult(vector=[0.1] * 8, model="t", model_version="1", dimensions=8) for _ in texts
    ]
    return IngestionOrchestrator(
        session=session,
        storage=AsyncMock(),
        parser=parser,
        chunker=StructureAwareChunker(SMALL),
        enricher=ExtractiveEnrichmentProvider(),
        embedder=embedder,
        vector_store=AsyncMock(),
    )


async def _corpus(session) -> Corpus:
    corpus = Corpus(name=f"parents-{uuid.uuid4().hex[:8]}", tenant_id=await new_tenant_id(session))
    session.add(corpus)
    await session.commit()
    return corpus


async def _links(session, corpus_id: uuid.UUID) -> dict[uuid.UUID, uuid.UUID]:
    rows = await session.execute(
        select(Chunk.id, Chunk.parent_chunk_id).where(
            Chunk.corpus_id == corpus_id, Chunk.parent_chunk_id.is_not(None)
        )
    )
    return {r.id: r.parent_chunk_id for r in rows}


async def _assert_section_linked(session, corpus_id: uuid.UUID) -> dict[uuid.UUID, uuid.UUID]:
    links = await _links(session, corpus_id)
    chunks = {
        c.id: c
        for c in (await session.execute(select(Chunk).where(Chunk.corpus_id == corpus_id)))
        .scalars()
        .all()
    }
    assert len(links) == 3, "the Returns section's three chunks point at its parent"
    (parent_id,) = set(links.values())
    parent = chunks[parent_id]
    assert parent.parent_chunk_id is None
    assert parent.text == "\n\n".join(
        chunks[c].text for c in sorted(links, key=lambda i: chunks[i].chunk_index)
    )
    return links


async def test_file_ingest_stores_parent_links(db_session) -> None:
    corpus = await _corpus(db_session)
    result = await _orchestrator(db_session).ingest(
        file_bytes=f"policy {uuid.uuid4()}".encode(),
        file_name="policy.md",
        mime_type="text/markdown",
        corpus_id=corpus.id,
        storage_key="k",
        mode=IngestMode.NEW,
    )
    assert result.status == JobStatus.COMPLETED, result.error
    await _assert_section_linked(db_session, corpus.id)


async def test_records_ingest_stores_parent_links(db_session) -> None:
    corpus = await _corpus(db_session)
    record = TextRecord(id="returns", title="Returns", text="\n\n".join(PARAGRAPHS))
    out = await _orchestrator(db_session).ingest_records([record], corpus.id, uuid.uuid4())
    assert out.status == "completed", out.error
    assert len(await _links(db_session, corpus.id)) == 3


async def test_backfill_recovers_links_ingest_never_stored(db_session) -> None:
    corpus = await _corpus(db_session)
    corpus_id = corpus.id
    result = await _orchestrator(db_session).ingest(
        file_bytes=f"policy {uuid.uuid4()}".encode(),
        file_name="policy.md",
        mime_type="text/markdown",
        corpus_id=corpus_id,
        storage_key="k",
        mode=IngestMode.NEW,
    )
    assert result.status == JobStatus.COMPLETED, result.error
    stored = await _assert_section_linked(db_session, corpus_id)
    # What every document ingested by earlier builds looks like.
    await db_session.execute(
        update(Chunk).where(Chunk.corpus_id == corpus_id).values(parent_chunk_id=None)
    )
    await db_session.commit()

    dry = await backfill_parent_links(db_session, corpus_id, apply=False)
    assert (dry.versions, dry.already_linked, dry.recovered_versions, dry.links) == (1, 0, 1, 3)
    assert await _links(db_session, corpus_id) == {}

    applied = await backfill_parent_links(db_session, corpus_id, apply=True)
    assert applied.links == 3
    db_session.expire_all()
    assert await _links(db_session, corpus_id) == stored

    again = await backfill_parent_links(db_session, corpus_id, apply=True)
    assert (again.already_linked, again.links) == (1, 0)
