"""During an embedding migration, retiring or adding a version's points reaches the
migration's new collection too.

Re-ingest, record updates and version restore used to remove old points only from the
corpus's own collection, so a version the migration had already copied survived in the
new one and came back at cutover; record imports and restore did not write new points
there at all.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from callobuzz_rag.chunking.chunker import StructureAwareChunker
from callobuzz_rag.db.models.document import Document
from callobuzz_rag.db.models.document_version import DocumentVersion
from callobuzz_rag.db.models.ingest_job import IngestJob
from callobuzz_rag.embeddings.base import EmbeddingResult
from callobuzz_rag.enrichment.models import EnrichmentResult
from callobuzz_rag.migration.context import DualWriteTarget
from callobuzz_rag.pipeline.orchestrator import IngestionOrchestrator
from callobuzz_rag.pipeline.records import TextRecord

MAIN = "corpus_x"
NEW = "corpus_x_v2"


def _embedder(model: str, dims: int) -> MagicMock:
    embedder = MagicMock()
    embedder.embed_texts = AsyncMock(
        side_effect=lambda texts: [
            EmbeddingResult(vector=[0.5] * dims, model=model, model_version="1", dimensions=dims)
            for _ in texts
        ]
    )
    embedder.close = AsyncMock()
    return embedder


def _orchestrator(existing: list[Document]) -> tuple[IngestionOrchestrator, MagicMock]:
    session = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.get = AsyncMock(return_value=IngestJob(id=uuid.uuid4(), status="queued"))
    rows = MagicMock()
    rows.scalars.return_value = existing
    session.execute = AsyncMock(return_value=rows)
    enricher = MagicMock()
    enricher.enrich_batch = AsyncMock(side_effect=lambda cs: [EnrichmentResult() for _ in cs])
    store = MagicMock()
    store.upsert = AsyncMock()
    store.delete_by_filter = AsyncMock()
    orch = IngestionOrchestrator(
        session=session,
        storage=MagicMock(),
        parser=MagicMock(),
        chunker=StructureAwareChunker(),
        enricher=enricher,
        embedder=_embedder("old", 2),
        vector_store=store,
        collection=MAIN,
        settings=MagicMock(),
    )
    return orch, store


def _migrating(target: DualWriteTarget | None = None):  # type: ignore[no-untyped-def]
    """Patch the migration seam: a dual-write window onto NEW is open."""
    target = target or DualWriteTarget(embedder=_embedder("new", 3), collection=NEW, model="new")
    return (
        patch(
            "callobuzz_rag.migration.context.get_dual_write_collection",
            AsyncMock(return_value=NEW),
        ),
        patch(
            "callobuzz_rag.migration.context.get_migration_context",
            AsyncMock(return_value=target),
        ),
    )


def _deleted(store: MagicMock) -> dict[str, list[object]]:
    out: dict[str, list[object]] = {}
    for call in store.delete_by_filter.await_args_list:
        collection, filters = call.args
        out.setdefault(collection, []).append(filters["document_version_id"])
    return out


async def test_deactivate_version_removes_points_from_the_migration_collection() -> None:
    orch, store = _orchestrator([])
    version = SimpleNamespace(id=uuid.uuid4(), status="active")
    p1, p2 = _migrating()
    with p1, p2:
        await orch._deactivate_version(version, uuid.uuid4())  # type: ignore[arg-type]
    assert version.status == "inactive"
    assert _deleted(store) == {MAIN: [str(version.id)], NEW: [[str(version.id)]]}


async def test_deactivate_version_without_migration_touches_only_the_corpus() -> None:
    orch, store = _orchestrator([])
    version = SimpleNamespace(id=uuid.uuid4(), status="active")
    with patch(
        "callobuzz_rag.migration.context.get_dual_write_collection", AsyncMock(return_value=None)
    ):
        await orch._deactivate_version(version, uuid.uuid4())  # type: ignore[arg-type]
    assert _deleted(store) == {MAIN: [str(version.id)]}


async def test_deactivate_version_survives_a_failed_migration_delete() -> None:
    orch, store = _orchestrator([])

    async def delete(collection: str, filters: dict[str, object]) -> None:
        if collection == NEW:
            raise RuntimeError("qdrant down")

    store.delete_by_filter = AsyncMock(side_effect=delete)
    version = SimpleNamespace(id=uuid.uuid4(), status="active")
    p1, p2 = _migrating()
    with p1, p2:
        await orch._deactivate_version(version, uuid.uuid4())  # type: ignore[arg-type]
    assert version.status == "inactive"


async def test_record_update_during_migration_reaches_the_new_collection() -> None:
    corpus_id = uuid.uuid4()
    old = TextRecord(id="faq", title="Old", text="Old answer.")
    new = TextRecord(id="faq", title="New", text="New answer.", metadata={"category": "faq"})
    doc = Document(
        id=uuid.uuid4(),
        corpus_id=corpus_id,
        external_id="faq",
        content_fingerprint=old.fingerprint(),
    )
    stale = DocumentVersion(id=uuid.uuid4(), document_id=doc.id, status="active")
    doc.versions = [stale]
    orch, store = _orchestrator([doc])
    target = DualWriteTarget(embedder=_embedder("new", 3), collection=NEW, model="new")

    p1, p2 = _migrating(target)
    with p1, p2:
        out = await orch.ingest_records([new], corpus_id, uuid.uuid4())

    assert out.status == "completed", out.error
    upserts = {c.args[0]: c.args[1] for c in store.upsert.await_args_list}
    assert set(upserts) == {MAIN, NEW}
    main_points, new_points = upserts[MAIN], upserts[NEW]
    assert [p.id for p in new_points] == [p.id for p in main_points]
    assert [p.payload for p in new_points] == [p.payload for p in main_points]
    assert all(len(p.vector) == 3 for p in new_points)  # the target model's vectors
    assert _deleted(store) == {MAIN: [[str(stale.id)]], NEW: [[str(stale.id)]]}
    target.embedder.close.assert_awaited()  # type: ignore[attr-defined]


async def test_file_ingest_dual_write_keeps_the_full_payload() -> None:
    """The dual-write points used to drop category, user_id and custom metadata."""
    corpus_id = uuid.uuid4()
    orch, store = _orchestrator([])
    parsed = TextRecord(id="x", title="Guide", text="Some text to index.").to_parsed()
    orch._parser.parse = AsyncMock(return_value=parsed)  # type: ignore[method-assign]
    rows = MagicMock()
    rows.scalar_one_or_none.return_value = None
    orch._session.execute = AsyncMock(return_value=rows)  # type: ignore[method-assign]

    from callobuzz_rag.pipeline.models import IngestMode

    p1, p2 = _migrating()
    with p1, p2:
        result = await orch.ingest(
            b"bytes",
            "guide.md",
            "text/markdown",
            corpus_id,
            storage_key="k",
            mode=IngestMode.NEW,
            doc_metadata={"category": "faq", "user_id": "u1", "team": "support"},
        )
    assert result.status == "completed", result.error
    upserts = {c.args[0]: c.args[1] for c in store.upsert.await_args_list}
    assert [p.payload for p in upserts[NEW]] == [p.payload for p in upserts[MAIN]]
    assert upserts[NEW][0].payload["category"] == "faq"
