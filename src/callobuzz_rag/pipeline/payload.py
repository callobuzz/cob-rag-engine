"""The Qdrant payload for a chunk that is already in Postgres.

Ingest builds payloads as it writes chunks. Everything that REBUILDS vectors later —
an embedding migration, restoring an older document version, the reindex command's
`--rebuild` — has only Postgres to go on, so the payload has to be derivable from it:
the chunk, its document, the document's metadata and the chunk's enrichment
keywords. Building it in one place keeps a rebuilt point identical to an ingested
one; the migration used to write `keywords=[]` and no metadata at all, so category,
user_id and custom metadata filters silently stopped matching after it ran.
"""

import uuid
from typing import Any

from callobuzz_rag.pipeline.text_hash import text_hash
from callobuzz_rag.vectordb.base import ChunkPayload


def chunk_payload(
    chunk: Any,
    corpus_id: uuid.UUID | str,
    document_id: uuid.UUID | str,
    doc_metadata: dict[str, Any] | None,
    keywords: list[str] | None = None,
) -> ChunkPayload:
    extra = dict(doc_metadata or {})
    category = extra.pop("category", None)
    user_id = extra.pop("user_id", None)
    # A chunk written before `chunks.text_hash` existed gets its hash here, from its text,
    # so a restored or rebuilt point is collapsible at once (`reindex --payload-hash`
    # fills the column itself).
    stored_hash = getattr(chunk, "text_hash", None)
    if stored_hash is None and isinstance(getattr(chunk, "text", None), str):
        stored_hash = text_hash(chunk.text)
    return ChunkPayload(
        chunk_id=str(chunk.id),
        corpus_id=str(corpus_id),
        document_id=str(document_id),
        document_version_id=str(chunk.document_version_id),
        page=chunk.page,
        is_table=chunk.is_table,
        is_code=chunk.is_code,
        language=chunk.language,
        keywords=list(keywords or []),
        category=category,
        user_id=user_id,
        metadata=extra,
        text_hash=stored_hash,
    )
