"""Post-ingestion review routes — create, finalize, and check status."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import get_session
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.content_portal.models.workspace import Workspace
from callobuzz_rag.content_portal.routes._access import (
    authorize_document_version,
    authorize_workspace,
    portal_actor,
    scoped_workspace,
)
from callobuzz_rag.content_portal.schemas.post_ingest import (
    CreatePostIngestReviewRequest,
    CreatePostIngestReviewResponse,
    FinalizeReviewRequest,
    FinalizeReviewResponse,
    PostIngestReviewStatus,
)
from callobuzz_rag.content_portal.services.post_ingest_review import (
    FinalizeFailedError,
    PostIngestReviewError,
    PostIngestReviewService,
    ReviewAlreadyExistsError,
)
from callobuzz_rag.db.models.corpus import Corpus

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/workspaces",
    tags=["portal-post-ingest"],
    dependencies=[Depends(authorize_workspace)],
)


def _handle_post_ingest_error(exc: Exception) -> HTTPException:
    """Map post-ingest review exceptions to HTTP errors (coded where the error has a code)."""
    if isinstance(exc, FinalizeFailedError):
        return APIError(exc.status_code, str(exc), exc.code or "", stage=exc.stage)
    if isinstance(exc, ReviewAlreadyExistsError) and exc.workspace_id is not None:
        return APIError(
            exc.status_code, str(exc), exc.code or "", workspace_id=str(exc.workspace_id)
        )
    if isinstance(exc, PostIngestReviewError):
        if exc.code:
            return APIError(exc.status_code, str(exc), exc.code)
        return HTTPException(status_code=exc.status_code, detail=str(exc))
    return HTTPException(status_code=500, detail="Internal server error")


def _get_service(request: Request, corpus: Corpus | None = None) -> PostIngestReviewService:
    """Build a PostIngestReviewService from app state providers.

    Uses the vector store and embedder initialized during app startup; with a
    corpus, the embedder that matches that corpus's collection (it may use its
    own model). Raises HTTPException(503) if providers are not available.
    """
    from callobuzz_rag.chunking.chunker import chunker_from_settings
    from callobuzz_rag.embeddings.factory import embedder_for_corpus

    app = request.app
    vector_store = getattr(app.state, "vector_store", None)
    embedder = getattr(app.state, "embedder", None)

    if vector_store is None or embedder is None:
        raise HTTPException(
            status_code=503,
            detail="Vector store or embedder not available",
        )
    settings = app.state.settings
    if corpus is not None:
        embedder = embedder_for_corpus(corpus, settings, embedder)

    return PostIngestReviewService(
        vector_store=vector_store,
        embedder=embedder,
        chunker=chunker_from_settings(settings),
        settings=settings,
    )


@router.post(
    "/from-ingestion",
    response_model=CreatePostIngestReviewResponse,
)
async def create_post_ingest_review(
    body: CreatePostIngestReviewRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> CreatePostIngestReviewResponse:
    """Create a review workspace from the active version of an ingested document.

    409 `version_not_active` when the version is not the document's active one;
    409 `review_exists` (with the open review's `workspace_id`) when one is already open.
    """
    corpus_id = await authorize_document_version(request, session, body.document_version_id)
    service = _get_service(request)
    try:
        result = await service.create_review_workspace(
            session=session,
            document_version_id=body.document_version_id,
            corpus_id=corpus_id,
            user=portal_actor(request, body.user),
            title=body.title,
        )
    except PostIngestReviewError as e:
        raise _handle_post_ingest_error(e) from e

    await session.commit()
    return CreatePostIngestReviewResponse(**result)


@router.post(
    "/{workspace_id}/finalize-review",
    response_model=FinalizeReviewResponse,
)
async def finalize_review(
    request: Request,
    body: FinalizeReviewRequest | None = None,
    workspace: Workspace = Depends(scoped_workspace),
    session: AsyncSession = Depends(get_session),
) -> FinalizeReviewResponse:
    """Publish a post-ingest review as a new version of the reviewed document.

    The workspace's accepted chunks (everything not rejected) become a new active
    version, embedded and indexed like an ingest; the reviewed version is kept inactive
    (restorable) and its vectors are removed. 409 `wrong_workspace_kind` for a workspace
    not created from an ingested document, `invalid_status_transition` when already
    finalized, `workspace_locked` when someone else holds the edit lock,
    `version_not_active` when the document changed since the review began,
    `reviewed_document_deleted` when it was deleted, `document_busy` when an ingest or
    restore of the document holds it for more than a few seconds, `review_changed` when
    chunks were edited while the new version was being built; 422
    `nothing_to_finalize` when every chunk was rejected; 503 `finalize_failed` when
    embedding or the vector store failed (nothing is changed).
    """
    corpus = await session.get(Corpus, workspace.corpus_id)
    if corpus is None:
        raise HTTPException(status_code=404, detail="Corpus not found")
    service = _get_service(request, corpus)
    settings = request.app.state.settings
    try:
        result = await service.finalize_review(
            session=session,
            workspace_id=workspace.id,
            user=portal_actor(request, body.user if body else None),
            collection_name=corpus.qdrant_collection,
            lock_timeout_minutes=settings.portal_lock_timeout_minutes,
        )
    except PostIngestReviewError as e:
        await session.rollback()
        raise _handle_post_ingest_error(e) from e

    # The document's content changed: cached answers about it are stale.
    from callobuzz_rag.api.routes.corpus import invalidate_corpus_query_cache

    await invalidate_corpus_query_cache(settings, str(corpus.id))
    return FinalizeReviewResponse(**result)


@router.get(
    "/{workspace_id}/review-status",
    response_model=PostIngestReviewStatus,
)
async def get_review_status(
    request: Request,
    workspace: Workspace = Depends(scoped_workspace),
    session: AsyncSession = Depends(get_session),
) -> PostIngestReviewStatus:
    """Get the current status of a post-ingestion review workspace."""
    service = _get_service(request)
    try:
        result = await service.get_review_status(
            session=session,
            workspace_id=workspace.id,
        )
    except PostIngestReviewError as e:
        raise _handle_post_ingest_error(e) from e

    return PostIngestReviewStatus(**result)
