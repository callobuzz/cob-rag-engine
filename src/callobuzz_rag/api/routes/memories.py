"""Memory management API — create, search, list, soft-delete."""

import logging
import math
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import CursorResult, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import get_session, path_corpus, require_owner
from callobuzz_rag.core.schemas import StrictRequest
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.memory import Memory, MemoryType

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/corpora/{corpus_id}/memories",
    tags=["memories"],
)


# --- Request/Response models ---


class CreateMemoryRequest(StrictRequest):
    content: str = Field(..., min_length=1, max_length=5000)
    memory_type: MemoryType = "fact"
    user_id: str = Field(..., min_length=1, max_length=255)
    source: str = Field(default="explicit", pattern="^(explicit|implicit)$")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    category: str | None = Field(default=None, max_length=50)
    related_entity: str | None = Field(default=None, max_length=255)


class BulkCreateMemoryRequest(StrictRequest):
    memories: list[CreateMemoryRequest] = Field(..., min_length=1, max_length=50)


class SearchMemoryRequest(StrictRequest):
    query: str = Field(..., min_length=1, max_length=2000)
    user_id: str = Field(..., min_length=1, max_length=255)
    limit: int = Field(default=5, ge=1, le=50)


class MemoryResponse(BaseModel):
    id: str
    corpus_id: str
    user_id: str | None = None
    memory_type: str
    content: str
    source: str | None = None
    confidence: float | None = None
    category: str | None = None
    related_entity: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: str

    model_config = {"from_attributes": True}


class MemoryListResponse(BaseModel):
    items: list[MemoryResponse]
    total: int
    limit: int
    offset: int


class BulkDeleteResponse(BaseModel):
    deleted: int


class SearchMemoryResponse(BaseModel):
    items: list[MemoryResponse]
    query: str
    total: int


# --- Helpers ---


def _corpus_embedder(request: Request, corpus: Corpus) -> Any:
    """The embedder matching the corpus's collection (a migration moves memories too)."""
    from callobuzz_rag.embeddings.factory import embedder_for_corpus

    default = getattr(request.app.state, "embedder", None)
    if default is None:
        return None
    return embedder_for_corpus(corpus, request.app.state.settings, default)


def _memory_to_response(m: Memory) -> MemoryResponse:
    metadata = m.metadata_ or {}
    return MemoryResponse(
        id=str(m.id),
        corpus_id=str(m.corpus_id),
        user_id=m.user_id,
        memory_type=m.memory_type,
        content=m.content,
        source=m.source,
        confidence=m.confidence,
        category=metadata.get("category"),
        related_entity=metadata.get("related_entity"),
        metadata=m.metadata_,
        created_at=m.created_at.isoformat(),
    )


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# --- Endpoints ---
# IMPORTANT: Fixed-path routes (/bulk, /search, /purge) MUST come before
# parameterized routes (/{memory_id}) to avoid FastAPI matching "bulk" as a UUID.


@router.post("", response_model=MemoryResponse, status_code=201)
async def create_memory(
    corpus_id: UUID,
    body: CreateMemoryRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    corpus: Corpus = Depends(path_corpus),
) -> MemoryResponse:
    """Create a memory with auto-embedding via TEI."""

    # Generate embedding if provider available
    embedding = None
    embedder = _corpus_embedder(request, corpus)
    if embedder is not None:
        try:
            result = await embedder.embed_query(body.content)
            embedding = result.vector
        except Exception as e:
            logger.warning("Memory embedding failed (storing without): %s", e)

    memory = Memory(
        corpus_id=corpus_id,
        user_id=body.user_id,
        memory_type=body.memory_type,
        content=body.content,
        embedding=embedding,
        source=body.source,
        confidence=body.confidence,
        metadata_={
            "category": body.category,
            "related_entity": body.related_entity,
        },
    )
    session.add(memory)
    await session.commit()
    await session.refresh(memory)

    return _memory_to_response(memory)


@router.post("/bulk", response_model=list[MemoryResponse], status_code=201)
async def bulk_create_memories(
    corpus_id: UUID,
    body: BulkCreateMemoryRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    corpus: Corpus = Depends(path_corpus),
) -> list[MemoryResponse]:
    """Create multiple memories at once (for implicit extraction results)."""

    embedder = _corpus_embedder(request, corpus)
    created = []

    for mem_req in body.memories:
        embedding = None
        if embedder is not None:
            try:
                result = await embedder.embed_query(mem_req.content)
                embedding = result.vector
            except Exception as e:
                logger.warning("Memory embedding failed: %s", e)

        memory = Memory(
            corpus_id=corpus_id,
            user_id=mem_req.user_id,
            memory_type=mem_req.memory_type,
            content=mem_req.content,
            embedding=embedding,
            source=mem_req.source,
            confidence=mem_req.confidence,
            metadata_={
                "category": mem_req.category,
                "related_entity": mem_req.related_entity,
            },
        )
        session.add(memory)
        created.append(memory)

    await session.commit()
    for m in created:
        await session.refresh(m)

    return [_memory_to_response(m) for m in created]


@router.post("/search", response_model=SearchMemoryResponse)
async def search_memories(
    corpus_id: UUID,
    body: SearchMemoryRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    corpus: Corpus = Depends(path_corpus),
) -> SearchMemoryResponse:
    """Semantic search memories by user — embeds query, cosine similarity ranked."""

    embedder = _corpus_embedder(request, corpus)
    query_embedding = None
    if embedder is not None:
        try:
            result = await embedder.embed_query(body.query)
            query_embedding = result.vector
        except Exception as e:
            logger.warning("Query embedding failed, falling back to text search: %s", e)

    # Load user's non-invalidated memories
    stmt = select(Memory).where(
        Memory.corpus_id == corpus_id,
        Memory.user_id == body.user_id,
        Memory.invalidated_at.is_(None),
    )
    rows = await session.execute(stmt)
    all_memories = rows.scalars().all()

    if query_embedding:
        # Rank by cosine similarity
        scored = []
        for m in all_memories:
            if m.embedding:
                score = _cosine_similarity(query_embedding, m.embedding)
                scored.append((score, m))
            else:
                scored.append((0.0, m))
        scored.sort(key=lambda x: x[0], reverse=True)
        ranked = [m for _, m in scored[: body.limit]]
    else:
        # Fallback: case-insensitive text search
        query_lower = body.query.lower()
        matched = [m for m in all_memories if query_lower in m.content.lower()]
        ranked = matched[: body.limit]

    return SearchMemoryResponse(
        items=[_memory_to_response(m) for m in ranked],
        query=body.query,
        total=len(ranked),
    )


@router.get("", response_model=MemoryListResponse)
async def list_memories(
    corpus_id: UUID,
    memory_type: MemoryType | None = Query(
        None, description="Filter by type: fact, preference, entity"
    ),
    user_id: str | None = Query(None, description="Filter by external user ID"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    corpus: Corpus = Depends(path_corpus),
) -> MemoryListResponse:
    """List non-invalidated memories, optionally filtered by type and/or user_id."""

    base_filter = [Memory.corpus_id == corpus_id, Memory.invalidated_at.is_(None)]

    if memory_type:
        base_filter.append(Memory.memory_type == memory_type)

    if user_id:
        base_filter.append(Memory.user_id == user_id)

    query = select(Memory).where(*base_filter)
    count_query = select(func.count(Memory.id)).where(*base_filter)

    total = (await session.execute(count_query)).scalar() or 0
    result = await session.execute(
        query.order_by(Memory.created_at.desc()).limit(limit).offset(offset)
    )
    memories = result.scalars().all()

    return MemoryListResponse(
        items=[_memory_to_response(m) for m in memories],
        total=total,
        limit=limit,
        offset=offset,
    )


# NOTE: /purge MUST come before /{memory_id} to avoid "purge" matching as UUID
@router.delete("/purge", response_model=BulkDeleteResponse)
async def purge_invalidated_memories(
    corpus_id: UUID,
    session: AsyncSession = Depends(get_session),
    corpus: Corpus = Depends(path_corpus),
    _owner: None = Depends(require_owner),
) -> BulkDeleteResponse:
    """Hard-delete all soft-deleted (invalidated) memories. Admin cleanup operation."""

    stmt = delete(Memory).where(
        Memory.corpus_id == corpus_id,
        Memory.invalidated_at.isnot(None),
    )
    result = await session.execute(stmt)
    await session.commit()
    return BulkDeleteResponse(deleted=cast("CursorResult[Any]", result).rowcount or 0)


@router.get("/{memory_id}", response_model=MemoryResponse)
async def get_memory(
    corpus_id: UUID,
    memory_id: UUID,
    session: AsyncSession = Depends(get_session),
    corpus: Corpus = Depends(path_corpus),
) -> MemoryResponse:
    """Get a single memory by ID (excludes invalidated)."""
    memory = await session.get(Memory, memory_id)
    if memory is None or memory.corpus_id != corpus_id or memory.invalidated_at is not None:
        raise HTTPException(status_code=404, detail="Memory not found")

    return _memory_to_response(memory)


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(
    corpus_id: UUID,
    memory_id: UUID,
    session: AsyncSession = Depends(get_session),
    corpus: Corpus = Depends(path_corpus),
) -> None:
    """Soft-delete a memory (set invalidated_at)."""
    memory = await session.get(Memory, memory_id)
    if memory is None or memory.corpus_id != corpus_id:
        raise HTTPException(status_code=404, detail="Memory not found")

    if memory.invalidated_at is not None:
        return  # Already invalidated

    memory.invalidated_at = datetime.now(UTC)
    await session.commit()


@router.delete("", response_model=BulkDeleteResponse)
async def bulk_delete_memories(
    corpus_id: UUID,
    memory_type: MemoryType | None = Query(
        None, description="Only this type: fact, preference, entity"
    ),
    user_id: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
    corpus: Corpus = Depends(path_corpus),
    _owner: None = Depends(require_owner),
) -> BulkDeleteResponse:
    """Bulk soft-delete memories by type and/or user_id."""

    stmt = (
        update(Memory)
        .where(Memory.corpus_id == corpus_id, Memory.invalidated_at.is_(None))
        .values(invalidated_at=datetime.now(UTC))
    )
    if memory_type:
        stmt = stmt.where(Memory.memory_type == memory_type)
    if user_id:
        stmt = stmt.where(Memory.user_id == user_id)

    result = await session.execute(stmt)
    await session.commit()
    return BulkDeleteResponse(deleted=cast("CursorResult[Any]", result).rowcount or 0)
