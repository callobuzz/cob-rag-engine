"""Guardrails for deleting with an API key, and the dry-run preview behind them.

Deleting a knowledge base, a document or a customer is the one thing an agent or script holding
an API key cannot undo, so a key (the admin key, `tk_` and `ck_` keys; anything with
`auth_kind == "api_key"`) must say what it is deleting: `?confirm_name=<the exact name>`.
Without it the delete is a 422 `confirmation_required`; with another name, a 422
`confirm_mismatch` (the code the dashboard's typed-confirm routes already use). Nothing is
deleted either way. A dashboard session keeps its own confirm dialog and is not asked.

`GET .../delete-preview` on each of the three says what a delete would remove (counts) and
the name to confirm with, and changes nothing.
"""

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.db.models.chunk import Chunk
from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.document import Document
from callobuzz_rag.db.models.document_version import DocumentVersion
from callobuzz_rag.db.models.memory import Memory
from callobuzz_rag.db.models.widget_key import WidgetKey

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession


def document_display_name(document: Document) -> str:
    """What a document is called when it is deleted: its file name, else its record id from
    `ingest/records`, else its id."""
    return str(document.file_name or document.external_id or document.id)


def needs_confirm_name(request: "Request") -> bool:
    """Whether this caller must name what it deletes: an API key, not a dashboard session."""
    return bool(getattr(request.state, "auth_kind", None) == "api_key")


def require_confirm_name(
    request: "Request", confirm_name: str | None, expected: str, what: str
) -> None:
    """Refuse a key's delete unless `confirm_name` is `expected` (outer spaces forgiven).

    `what` names the thing in the message: "knowledge base", "document", "customer".
    """
    if not needs_confirm_name(request):
        return
    if confirm_name is None or not confirm_name.strip():
        raise APIError(
            422,
            f"Deleting a {what} with an API key needs confirm_name={expected!r} "
            f"(see the delete-preview route to check what would be removed)",
            "confirmation_required",
            confirm_name=expected,
        )
    if confirm_name.strip() != expected:
        raise APIError(
            422,
            f"confirm_name does not match this {what}'s name; nothing was deleted",
            "confirm_mismatch",
        )


async def _count(session: "AsyncSession", column: Any, *where: Any) -> int:
    value = (await session.execute(select(func.count(column)).where(*where))).scalar_one()
    return int(value or 0)


async def corpus_counts(session: "AsyncSession", corpus_ids: list[uuid.UUID]) -> dict[str, int]:
    """What deleting these knowledge bases removes."""
    return {
        "documents": await _count(session, Document.id, Document.corpus_id.in_(corpus_ids)),
        "chunks": await _count(session, Chunk.id, Chunk.corpus_id.in_(corpus_ids)),
        "widget_keys": await _count(session, WidgetKey.id, WidgetKey.corpus_id.in_(corpus_ids)),
        "conversations": await _count(
            session, Conversation.id, Conversation.corpus_id.in_(corpus_ids)
        ),
        "memories": await _count(session, Memory.id, Memory.corpus_id.in_(corpus_ids)),
    }


async def tenant_counts(session: "AsyncSession", tenant_id: uuid.UUID) -> dict[str, int]:
    """What deleting this customer removes: its knowledge bases and everything in them, its
    team and its API keys."""
    from callobuzz_rag.db.models.api_key import ApiKey
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.tenant_member import TenantMember

    corpus_ids = list(
        (await session.execute(select(Corpus.id).where(Corpus.tenant_id == tenant_id)))
        .scalars()
        .all()
    )
    counts = {"knowledge_bases": len(corpus_ids)}
    counts.update(await corpus_counts(session, corpus_ids))
    counts["members"] = await _count(
        session, TenantMember.user_id, TenantMember.tenant_id == tenant_id
    )
    counts["api_keys"] = await _count(session, ApiKey.id, ApiKey.tenant_id == tenant_id)
    return counts


async def document_counts(session: "AsyncSession", document_id: uuid.UUID) -> dict[str, int]:
    """What deleting this document removes: its versions and their chunks."""
    versions = select(DocumentVersion.id).where(DocumentVersion.document_id == document_id)
    return {
        "versions": await _count(
            session, DocumentVersion.id, DocumentVersion.document_id == document_id
        ),
        "chunks": await _count(session, Chunk.id, Chunk.document_version_id.in_(versions)),
    }
