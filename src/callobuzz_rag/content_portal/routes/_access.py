"""Ownership checks shared by the Content Portal routes.

Every portal route authorizes through the engine's one scoping rule,
`authorized_corpus_ids(resolve_scope(request), ...)` in `api/deps.py` (admin: all;
tenant: its corpora; corpus key: itself), by one of three entry points:

- a workspace in the path: `authorize_workspace` runs as a router-level dependency on
  every router whose paths carry `{workspace_id}`, and handlers that need the row take
  `scoped_workspace`, which applies the same check and returns it;
- `?corpus_id=` on workspace create and list: `scoped_corpus` / `authorized_corpus_ids`;
- the corpus in the path (content rules): `path_corpus`.

No portal route takes the corpus from the key alone (the corpus-key-only dependency
in `api/deps.py` refuses admin and tenant keys).
`portal_actor` names who did something: a signed-in user's own e-mail, never a name
the caller typed.
"""

import uuid
from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import authorized_corpus_ids, get_session, resolve_scope

if TYPE_CHECKING:
    from callobuzz_rag.content_portal.models.workspace import Workspace

API_KEY_ACTOR = "api_key_user"


async def _load_workspace(
    request: Request, workspace_id: uuid.UUID, session: AsyncSession
) -> "Workspace":
    from callobuzz_rag.content_portal.models.workspace import Workspace

    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    await authorized_corpus_ids(resolve_scope(request), workspace.corpus_id, session)
    return workspace


async def authorize_workspace(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Refuse the request unless the `{workspace_id}` in the path is in the caller's scope.

    Routes without a workspace in their path (list, create, from-ingestion)
    pass through here and authorize on their own.

    Raises:
        HTTPException(404): The workspace does not exist.
        HTTPException(403): It belongs to a corpus outside the caller's scope.
    """
    raw = request.path_params.get("workspace_id")
    if raw is None:
        return
    try:
        workspace_id = uuid.UUID(str(raw))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid workspace id") from exc
    await _load_workspace(request, workspace_id, session)


async def scoped_workspace(
    request: Request,
    workspace_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> "Workspace":
    """The `{workspace_id}` workspace, if the caller's scope reaches its corpus.

    Handlers use `workspace.corpus_id` rather than the key's own corpus, so admin
    and tenant keys work as well as the workspace's corpus key.

    Raises:
        HTTPException(404): The workspace does not exist.
        HTTPException(403): It belongs to a corpus outside the caller's scope.
    """
    return await _load_workspace(request, workspace_id, session)


def portal_actor(request: Request, supplied: str | None = None) -> str:
    """Who is acting, for audit trails, locks and `created_by`.

    A signed-in user (`request.state.user`, set by the middleware for sessions) is
    always recorded by their account e-mail and `supplied` is ignored, so nobody can
    act as someone else. An API key names its own users: `supplied` (the body's
    `user`), else the `X-User-Id` header, else "api_key_user".
    """
    user = getattr(request.state, "user", None)
    if user is not None:
        return str(user.email)
    if supplied:
        return supplied
    header = request.headers.get("X-User-Id", "").strip()
    if header:
        return header[:255]
    return API_KEY_ACTOR


async def authorize_document_version(
    request: Request,
    session: AsyncSession,
    document_version_id: uuid.UUID,
) -> uuid.UUID:
    """Return the corpus that owns a document version, after checking the caller may use it.

    Raises:
        HTTPException(404): The version or its document does not exist.
        HTTPException(403): It belongs to a corpus outside the caller's scope.
    """
    from callobuzz_rag.db.models.document import Document
    from callobuzz_rag.db.models.document_version import DocumentVersion

    version = await session.get(DocumentVersion, document_version_id)
    document = await session.get(Document, version.document_id) if version else None
    if document is None:
        raise HTTPException(status_code=404, detail="Document version not found")
    await authorized_corpus_ids(resolve_scope(request), document.corpus_id, session)
    return document.corpus_id
