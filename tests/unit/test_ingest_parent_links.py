"""Ingest stores the chunker's parent links.

`_build_chunk_models` used to link each child to its parent's `id` before the database
had assigned one, so every child got `parent_chunk_id = None`: no ingested chunk ever
had a parent, and retrieval's parent expansion never ran.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

from callobuzz_rag.chunking.chunker import ChunkerConfig, StructureAwareChunker
from callobuzz_rag.db.models.chunk import Chunk
from callobuzz_rag.db.models.ingest_job import IngestJob
from callobuzz_rag.embeddings.base import EmbeddingResult
from callobuzz_rag.enrichment.models import EnrichmentResult
from callobuzz_rag.parsing.models import ElementType, ParsedDocument, ParsedElement
from callobuzz_rag.pipeline.orchestrator import IngestionOrchestrator
from callobuzz_rag.pipeline.records import TextRecord

# Small sizes, so one section is cut into several chunks and gets a parent.
SMALL = ChunkerConfig(target_tokens=30, min_tokens=5, max_tokens=60, overlap_tokens=0)
PARAGRAPHS = [
    f"Paragraph {i} explains rule {i} of the returns policy in plain words, "
    f"with enough detail that it needs a chunk of its own, number {i}."
    for i in range(3)
]


def _orchestrator(chunker: StructureAwareChunker) -> tuple[IngestionOrchestrator, MagicMock]:
    session = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.get = AsyncMock(return_value=IngestJob(id=uuid.uuid4(), status="queued"))
    rows = MagicMock()
    rows.scalars.return_value = []
    session.execute = AsyncMock(return_value=rows)
    enricher = MagicMock()
    enricher.enrich_batch = AsyncMock(side_effect=lambda cs: [EnrichmentResult() for _ in cs])
    embedder = MagicMock()
    embedder.embed_texts = AsyncMock(
        side_effect=lambda texts: [
            EmbeddingResult(vector=[0.1, 0.2], model="m", model_version="1", dimensions=2)
            for _ in texts
        ]
    )
    store = MagicMock()
    store.upsert = AsyncMock()
    store.delete_by_filter = AsyncMock()
    orch = IngestionOrchestrator(
        session=session,
        storage=MagicMock(),
        parser=MagicMock(),
        chunker=chunker,
        enricher=enricher,
        embedder=embedder,
        vector_store=store,
        collection="corpus_x",
    )
    return orch, session


def _added_chunks(session: MagicMock) -> list[Chunk]:
    return [
        obj
        for call in session.add_all.call_args_list
        for obj in call.args[0]
        if isinstance(obj, Chunk)
    ]


def _assert_linked(chunks: list[Chunk], parent_flags: list[bool]) -> None:
    by_id = {c.id: i for i, c in enumerate(chunks)}
    assert all(c.id is not None for c in chunks)
    parents = [c for c, is_parent in zip(chunks, parent_flags, strict=True) if is_parent]
    assert parents, "the fixture must produce a parent chunk"
    linked = [c for c in chunks if c.parent_chunk_id is not None]
    assert linked, "no child was linked to its parent"
    for child in linked:
        parent_pos = by_id[child.parent_chunk_id]
        assert parent_flags[parent_pos]
        # A parent is inserted before its children: one flush satisfies the FK.
        assert parent_pos < by_id[child.id]
    # Every non-parent chunk of a section that has a parent points at it.
    assert len(linked) == len(chunks) - len(parents)


def test_build_chunk_models_links_children_to_real_parent_ids() -> None:
    chunker = StructureAwareChunker(SMALL)
    parsed = ParsedDocument(
        elements=[
            ParsedElement(type=ElementType.HEADING, text="Returns", level=1),
            *[ParsedElement(type=ElementType.PARAGRAPH, text=p) for p in PARAGRAPHS],
        ],
        source_filename="returns.md",
        mime_type="text/markdown",
        parser_name="test",
        parser_version="1",
    )
    results = chunker.chunk(parsed)
    orch, _ = _orchestrator(chunker)

    models = orch._build_chunk_models(results, uuid.uuid4(), uuid.uuid4())

    _assert_linked(models, [r.is_parent for r in results])
    for model, result in zip(models, results, strict=True):
        if result.parent_chunk_index is not None:
            assert model.parent_chunk_id == models[result.parent_chunk_index].id


async def test_ingest_records_stores_parent_links() -> None:
    chunker = StructureAwareChunker(SMALL)
    record = TextRecord(id="returns", title="Returns", text="\n\n".join(PARAGRAPHS))
    orch, session = _orchestrator(chunker)

    out = await orch.ingest_records([record], uuid.uuid4(), uuid.uuid4())

    assert out.status == "completed", out.error
    chunks = _added_chunks(session)
    expected = chunker.chunk(record.to_parsed())
    _assert_linked(chunks, [r.is_parent for r in expected])
