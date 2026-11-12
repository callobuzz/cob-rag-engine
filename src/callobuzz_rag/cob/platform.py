"""Platform docs: what the operator adds to COB's knowledge base next to the built-in docs.

COB's knowledge base holds two kinds of document. **Built-in** docs come from the engine's own
files (`cob/sync.py`), are tagged `doc_metadata.cob_source = "builtin"`, and are re-synced on
every upgrade. **Platform** docs are added by the super admin through the normal ingestion
paths, tagged `"platform"` with an `audience` and the person who added them. The sync only
ever touches builtin documents, so an upgrade never changes or deletes a platform doc.

Only a super-admin session may write to the system knowledge base, and only platform
documents; builtin ones answer 403 `cob_builtin_readonly`. Every other principal keeps the
409 `system_resource_protected` of `cob/system.py`.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import select

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.cob.system import is_system_corpus, refuse_system_corpus
from callobuzz_rag.cob.types import Audience
from callobuzz_rag.pipeline.records import SYSTEM_RECORD_PREFIX

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

SOURCE_KEY: Final = "cob_source"
BUILTIN: Final = "builtin"
PLATFORM: Final = "platform"
DEFAULT_AUDIENCE: Final = Audience.CUSTOMER
BUILTIN_ID_PREFIX: Final = f"{SYSTEM_RECORD_PREFIX}cob/"
PLATFORM_SLUG_PREFIX: Final = "platform/"
# The only routes through `path_corpus` / `scoped_corpus` a super admin may use on COB's
# knowledge base (the route itself refuses built-in documents).
PLATFORM_DOC_ROUTES: Final = frozenset({("DELETE", "/api/v1/documents/{document_id}")})


def is_super_admin_session(request: "Request") -> bool:
    """A signed-in super admin in the operator view (not an API key, not a workspace)."""
    state = request.state
    return (
        getattr(state, "auth_kind", None) == "session"
        and getattr(state, "is_admin", False) is True
        and getattr(state, "user", None) is not None
    )


def doc_source(external_id: str | None, doc_metadata: dict[str, Any] | None) -> str:
    """`builtin` or `platform`. A document the engine synced is builtin even before the
    backfill tagged it (its id starts with `system:cob/`)."""
    if (external_id or "").startswith(BUILTIN_ID_PREFIX):
        return BUILTIN
    if (doc_metadata or {}).get(SOURCE_KEY) == BUILTIN:
        return BUILTIN
    return PLATFORM


def refuse_builtin(external_id: str | None, doc_metadata: dict[str, Any] | None) -> None:
    if doc_source(external_id, doc_metadata) == BUILTIN:
        raise APIError(
            403,
            "This page comes with the engine and is updated automatically. "
            "You can add your own pages next to it.",
            "cob_builtin_readonly",
        )


def allow_system_write(request: "Request", corpus: Any) -> None:
    """Replaces `refuse_system_corpus` on the routes a platform doc is added through: COB's
    knowledge base is writable for a super-admin session, refused (409) for everyone else."""
    if is_system_corpus(corpus) and is_super_admin_session(request):
        return
    refuse_system_corpus(corpus)


def parse_audience(value: str | None) -> Audience:
    if value is None or value == "":
        return DEFAULT_AUDIENCE
    try:
        return Audience(value)
    except ValueError as exc:
        raise APIError(
            422,
            "The audience must be admin, customer or public.",
            "invalid_audience",
        ) from exc


def platform_metadata(request: "Request", supplied: dict[str, Any] | None = None) -> dict[str, str]:
    """The metadata a platform document is stored with. `audience` is the caller's choice
    (default customer); the source tag and the author are always set here, never taken from
    the request."""
    user = getattr(request.state, "user", None)
    meta = {
        str(k): str(v)
        for k, v in (supplied or {}).items()
        if k not in (SOURCE_KEY, "uploaded_by", "audience", "cob", "slug", "anchor", "route")
    }
    meta["audience"] = parse_audience((supplied or {}).get("audience")).value
    meta[SOURCE_KEY] = PLATFORM
    meta["uploaded_by"] = str(user.id) if user is not None else ""
    return meta


async def backfill_builtin_tag(session: "AsyncSession", corpus_id: uuid.UUID) -> int:
    """Tag the engine's documents that were synced before platform docs existed. Idempotent;
    the caller commits."""
    from callobuzz_rag.db.models.document import Document

    rows = (
        (await session.execute(select(Document).where(Document.corpus_id == corpus_id)))
        .scalars()
        .all()
    )
    tagged = 0
    for doc in rows:
        if not (doc.external_id or "").startswith(BUILTIN_ID_PREFIX):
            continue
        meta = dict(doc.doc_metadata or {})
        if meta.get(SOURCE_KEY) == BUILTIN:
            continue
        meta[SOURCE_KEY] = BUILTIN
        doc.doc_metadata = meta
        tagged += 1
    return tagged


@dataclass(frozen=True)
class PlatformHit:
    """A retrieved chunk of a platform doc, as the page COB can cite or list."""

    slug: str
    title: str


def platform_hit(
    *,
    external_id: str | None,
    document_id: Any,
    file_name: str | None,
    audience: str | None,
    allowed: Sequence[str],
) -> PlatformHit | None:
    """`PlatformHit` for a chunk of a platform doc the caller may read, else None.

    A built-in chunk (its id starts with `system:`) is never a platform hit. The audience is
    checked again here although the retrieval filter already applied it (defence in depth);
    a doc stored without one is `customer`, like everywhere else."""
    if document_id is None or (external_id or "").startswith(SYSTEM_RECORD_PREFIX):
        return None
    if str(audience or DEFAULT_AUDIENCE) not in allowed:
        return None
    title = file_name or external_id or "Untitled"
    return PlatformHit(slug=f"{PLATFORM_SLUG_PREFIX}{document_id}", title=str(title))
