"""Request/response schemas for post-ingestion review endpoints."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from callobuzz_rag.core.schemas import StrictRequest

_USER_DESCRIPTION = (
    "Who is acting, for the audit trail (API keys only; a signed-in user is always recorded "
    "by their account). Defaults to the X-User-Id header, else 'api_key_user'."
)


class CreatePostIngestReviewRequest(StrictRequest):
    """Request to create a post-ingestion review workspace."""

    document_version_id: UUID = Field(
        ..., description="The document's ACTIVE version (409 version_not_active otherwise)."
    )
    user: str | None = Field(
        default=None, min_length=1, max_length=255, description=_USER_DESCRIPTION
    )
    title: str | None = Field(default=None, max_length=500)


class CreatePostIngestReviewResponse(BaseModel):
    """Response after creating a post-ingestion review workspace."""

    model_config = ConfigDict(from_attributes=True)

    workspace_id: UUID
    document_version_id: UUID
    corpus_id: UUID
    title: str
    status: str
    chunks_copied: int
    message: str


class FinalizeReviewRequest(BaseModel):
    """Request to finalize a post-ingestion review (the body is optional)."""

    user: str | None = Field(
        default=None, min_length=1, max_length=255, description=_USER_DESCRIPTION
    )


class DiscardedParentEdit(BaseModel):
    """A staged chunk descending from a parent whose own text was not published."""

    staged_chunk_id: UUID
    parent_chunk_id: UUID = Field(..., description="The reviewed version's parent chunk.")
    reason: str = Field(
        ...,
        description=(
            "The last text change in its edit history (edit_text, rewrite, split, merge, ...), "
            "or 'edited'."
        ),
    )


class FinalizeReviewResponse(BaseModel):
    """The new version a finalized review produced, and what went into it."""

    model_config = ConfigDict(from_attributes=True)

    workspace_id: UUID
    status: str
    message: str
    document_id: UUID
    previous_version_id: UUID = Field(
        ..., description="The reviewed version, now inactive (restorable)."
    )
    new_version_id: UUID = Field(..., description="The version built from the review, active.")
    chunks_in_new_version: int = Field(
        ..., description="Chunks of the new version, parents included."
    )
    chunks_unchanged: int = Field(..., description="Carried over with their text untouched.")
    chunks_edited: int = Field(
        ..., description="Edited, split or merged from a chunk of the reviewed version."
    )
    chunks_added: int = Field(..., description="Added in the review (no source chunk).")
    parent_chunks: int = Field(
        ..., description="Parent chunks re-derived from their surviving children."
    )
    staged_chunks_rejected: int = Field(..., description="Staged chunks rejected in the review.")
    previous_chunks_dropped: int = Field(
        ...,
        description=(
            "Chunks of the reviewed version with no counterpart in the new one (rejected, "
            "deleted, or a parent that no longer has two children)."
        ),
    )
    staged_chunks_total: int
    discarded_parent_edits: list[DiscardedParentEdit] = Field(
        default_factory=list,
        description=(
            "Staged chunks descending from a parent chunk whose own text differed from the "
            "parent's and was not published: a parent's text is rebuilt from its surviving "
            "children. The editor refuses such edits (409 parent_chunk_derived); these come "
            "from reviews edited before it did."
        ),
    )


class PostIngestReviewStatus(BaseModel):
    """Current status of a post-ingestion review workspace."""

    model_config = ConfigDict(from_attributes=True)

    workspace_id: UUID
    kind: str
    document_version_id: UUID | None = Field(
        ..., description="The version under review (null once its document is deleted)."
    )
    finalized_version_id: UUID | None = Field(
        default=None, description="The version finalizing produced, once finalized."
    )
    corpus_id: UUID
    status: str
    total_chunks: int
    approved_chunks: int
    rejected_chunks: int
    draft_chunks: int
    modified_chunks: int
