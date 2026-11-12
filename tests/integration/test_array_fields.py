"""Test PostgreSQL ARRAY field roundtrip with asyncpg.

Verifies that list[str] fields (keywords, tags, hypothetical_questions)
serialize and deserialize correctly through SQLAlchemy + asyncpg.
"""

import pytest

from callobuzz_rag.db.models.chunk import Chunk
from callobuzz_rag.db.models.chunk_metadata import ChunkMetadata
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.document import Document
from callobuzz_rag.db.models.document_version import DocumentVersion
from tests.integration.conftest import new_tenant_id


@pytest.mark.integration
async def test_array_field_roundtrip(db_session):
    """ARRAY(Text) fields should roundtrip as Python lists."""
    # Create required parent records
    corpus = Corpus(name="Array Test Corpus", tenant_id=await new_tenant_id(db_session))
    db_session.add(corpus)
    await db_session.flush()

    doc = Document(corpus_id=corpus.id, file_name="test.pdf", mime_type="application/pdf")
    db_session.add(doc)
    await db_session.flush()

    version = DocumentVersion(document_id=doc.id, status="processing")
    db_session.add(version)
    await db_session.flush()

    chunk = Chunk(
        document_version_id=version.id,
        corpus_id=corpus.id,
        chunk_index=0,
        text="Test chunk text",
        structure_path=["Document", "Section 1", "Paragraph"],
    )
    db_session.add(chunk)
    await db_session.flush()

    # Test ChunkMetadata ARRAY fields
    meta = ChunkMetadata(
        chunk_id=chunk.id,
        keywords=["machine learning", "AI", "neural networks"],
        hypothetical_questions=["What is ML?", "How does AI work?"],
        tags=["important", "reviewed"],
    )
    db_session.add(meta)
    await db_session.commit()

    # Re-fetch from DB
    fetched_meta = await db_session.get(ChunkMetadata, chunk.id)
    assert fetched_meta is not None
    assert fetched_meta.keywords == ["machine learning", "AI", "neural networks"]
    assert fetched_meta.hypothetical_questions == ["What is ML?", "How does AI work?"]
    assert fetched_meta.tags == ["important", "reviewed"]

    # Verify chunk structure_path array too
    fetched_chunk = await db_session.get(Chunk, chunk.id)
    assert fetched_chunk is not None
    assert fetched_chunk.structure_path == ["Document", "Section 1", "Paragraph"]
