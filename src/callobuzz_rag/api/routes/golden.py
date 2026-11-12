"""Golden dataset endpoints — CRUD for golden Q&A pairs."""

import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from callobuzz_rag.db.models.golden_pair import GoldenPair

from callobuzz_rag.api.deps import (
    Scope,
    authorized_corpus_ids,
    get_session,
    require_owner,
    resolve_scope,
)
from callobuzz_rag.api.schemas.evals import GoldenPairFields
from callobuzz_rag.api.schemas.golden import (
    GoldenExportResponse,
    GoldenFromFeedbackRequest,
    GoldenImportResponse,
    GoldenPairCreateRequest,
    GoldenPairDeleteResponse,
    GoldenPairListResponse,
    GoldenPairResponse,
    GoldenPairUpdateRequest,
)
from callobuzz_rag.cob.system import refuse_system_corpus_id
from callobuzz_rag.evaluation.golden import GoldenDatasetManager, GoldenImportError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/golden", tags=["golden"])

# Passed to `create` as arguments of their own, not through `fields`.
_CREATE_ARGS = {"query", "expected_answer"}


async def _require_corpus_access(
    scope: Scope,
    corpus_id: UUID,
    session: AsyncSession,
) -> None:
    """403 unless `corpus_id` is inside the caller's scope.

    Every golden route names a corpus, so authorization is always this check —
    `authorized_corpus_ids` raises for a tenant asking about someone else's
    corpus, and returns the permitted set otherwise.
    """
    allowed = await authorized_corpus_ids(scope, corpus_id, session)
    if allowed is not None and corpus_id not in allowed:
        raise HTTPException(status_code=403, detail="Corpus is outside this key's scope.")


def _manager(session: AsyncSession, request: Request) -> GoldenDatasetManager:
    """The manager with the engine's per-set question limit (`golden_set_max_pairs`)."""
    return GoldenDatasetManager(session, max_pairs=request.app.state.settings.golden_set_max_pairs)


def _pair_to_response(pair: "GoldenPair") -> GoldenPairResponse:
    """Convert a GoldenPair model to a response schema."""
    return GoldenPairResponse(
        id=pair.id,
        corpus_id=pair.corpus_id,
        query=pair.query,
        expected_answer=pair.expected_answer,
        expected_chunk_ids=pair.expected_chunk_ids,
        metadata=pair.pair_metadata,
        source=pair.source,
        created_at=pair.created_at.isoformat(),
        set_id=pair.set_id,
        expected_behaviour=pair.expected_behaviour,
        expected_label=pair.expected_label,
        must_include=list(pair.must_include or []),
        must_cite=pair.must_cite,
        origin=pair.origin,
        difficulty=pair.difficulty,
        topic=pair.topic,
    )


@router.post("", response_model=GoldenPairResponse, status_code=201)
async def create_golden_pair(
    body: GoldenPairCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    scope: Scope = Depends(resolve_scope),
) -> GoldenPairResponse:
    """Create a new golden Q&A pair."""
    await _require_corpus_access(scope, body.corpus_id, session)
    await refuse_system_corpus_id(session, body.corpus_id)
    if body.set_id is not None:
        from callobuzz_rag.api.routes.evals import load_set

        await load_set(session, body.corpus_id, body.set_id)
    manager = _manager(session, request)
    fields = body.model_dump(include=set(GoldenPairFields.model_fields) - _CREATE_ARGS)
    fields["history"] = [turn.model_dump() for turn in body.history]
    pair = await manager.create(
        corpus_id=body.corpus_id,
        query=body.query,
        expected_answer=body.expected_answer,
        expected_chunk_ids=body.expected_chunk_ids,
        pair_metadata=body.metadata,
        source="manual",
        set_id=body.set_id,
        fields=fields,
    )
    return _pair_to_response(pair)


@router.get("", response_model=GoldenPairListResponse)
async def list_golden_pairs(
    corpus_id: UUID = Query(..., description="Filter by corpus ID."),
    limit: int = Query(50, ge=1, le=500, description="Max results."),
    offset: int = Query(0, ge=0, description="Pagination offset."),
    session: AsyncSession = Depends(get_session),
    scope: Scope = Depends(resolve_scope),
) -> GoldenPairListResponse:
    """List golden pairs for a corpus."""
    await _require_corpus_access(scope, corpus_id, session)
    manager = GoldenDatasetManager(session)
    pairs = await manager.list_by_corpus(corpus_id, limit=limit, offset=offset)
    return GoldenPairListResponse(
        items=[_pair_to_response(p) for p in pairs],
        count=len(pairs),
        total=await manager.count_by_corpus(corpus_id),
        offset=offset,
        limit=limit,
    )


@router.patch("/{pair_id}", response_model=GoldenPairResponse)
async def update_golden_pair(
    pair_id: UUID,
    body: GoldenPairUpdateRequest,
    session: AsyncSession = Depends(get_session),
    scope: Scope = Depends(resolve_scope),
) -> GoldenPairResponse:
    """Edit a golden pair's question, expected answer, chunk ids or metadata."""
    from callobuzz_rag.db.models.golden_pair import GoldenPair

    pair = await session.get(GoldenPair, pair_id)
    if pair is None:
        raise HTTPException(status_code=404, detail=f"Golden pair {pair_id} not found")
    await _require_corpus_access(scope, pair.corpus_id, session)

    changes = body.model_dump(exclude_unset=True)
    if "query" in changes and changes["query"] is None:
        raise HTTPException(status_code=422, detail="query cannot be null")
    # A pair keeps at least one expectation: clearing the answer needs a behaviour (C7).
    answer = changes.get("expected_answer", pair.expected_answer)
    behaviour = changes.get("expected_behaviour", pair.expected_behaviour)
    if answer is None and behaviour is None:
        raise HTTPException(
            status_code=422, detail="give an expected answer, an expected behaviour, or both"
        )
    if changes.get("must_include", 0) is None:
        changes["must_include"] = []
    if not changes:
        return _pair_to_response(pair)
    pair = await GoldenDatasetManager(session).update(pair, changes)
    return _pair_to_response(pair)


@router.delete("/{pair_id}", response_model=GoldenPairDeleteResponse)
async def delete_golden_pair(
    pair_id: UUID,
    session: AsyncSession = Depends(get_session),
    scope: Scope = Depends(resolve_scope),
    _owner: None = Depends(require_owner),
) -> GoldenPairDeleteResponse:
    """Delete a golden pair by ID. Owner-only, like deleting a document (accounts A8)."""
    from callobuzz_rag.db.models.golden_pair import GoldenPair

    existing = await session.get(GoldenPair, pair_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Golden pair {pair_id} not found")
    await _require_corpus_access(scope, existing.corpus_id, session)

    manager = GoldenDatasetManager(session)
    try:
        await manager.delete(pair_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return GoldenPairDeleteResponse()


@router.post("/import", response_model=GoldenImportResponse, status_code=201)
async def import_golden_pairs(
    request: Request,
    corpus_id: UUID = Query(..., description="Corpus to import into."),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    scope: Scope = Depends(resolve_scope),
) -> GoldenImportResponse:
    """Import golden pairs from a CSV file upload."""
    await _require_corpus_access(scope, corpus_id, session)
    await refuse_system_corpus_id(session, corpus_id)
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a .csv")

    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="wb") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        manager = _manager(session, request)
        count = await manager.import_from_csv(corpus_id, tmp_path)
    except GoldenImportError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    finally:
        tmp_path.unlink(missing_ok=True)

    return GoldenImportResponse(imported_count=count)


@router.get("/export", response_model=GoldenExportResponse)
async def export_golden_pairs(
    corpus_id: UUID = Query(..., description="Corpus to export."),
    session: AsyncSession = Depends(get_session),
    scope: Scope = Depends(resolve_scope),
) -> GoldenExportResponse:
    """Export all golden pairs for a corpus as JSON."""
    await _require_corpus_access(scope, corpus_id, session)
    manager = GoldenDatasetManager(session)
    items = await manager.export_to_json(corpus_id)
    return GoldenExportResponse(items=items, count=len(items))


@router.post("/from-feedback", response_model=GoldenPairResponse, status_code=201)
async def create_from_feedback(
    body: GoldenFromFeedbackRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    scope: Scope = Depends(resolve_scope),
) -> GoldenPairResponse:
    """Promote a feedback correction to a golden pair."""
    from callobuzz_rag.db.models.feedback import Feedback
    from callobuzz_rag.db.models.query_log import QueryLog

    # The request names a feedback row, so the corpus has to be resolved
    # through it before this can be authorized at all.
    feedback = await session.get(Feedback, body.feedback_id)
    if feedback is None:
        raise HTTPException(status_code=404, detail="Feedback not found")
    query_log = (
        await session.get(QueryLog, feedback.query_log_id) if feedback.query_log_id else None
    )
    if query_log is None or query_log.corpus_id is None:
        raise HTTPException(
            status_code=400,
            detail="Feedback has no linked query with a corpus.",
        )
    await _require_corpus_access(scope, query_log.corpus_id, session)

    manager = _manager(session, request)
    try:
        pair = await manager.create_from_feedback(body.feedback_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _pair_to_response(pair)
