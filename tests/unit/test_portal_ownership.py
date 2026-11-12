"""Content Portal routes only reach workspaces inside the caller's scope.

Before: chunks, review, AI chat and post-ingest routes had no ownership check,
so any valid key could read, edit, approve or ingest another client's
workspace, and from-ingestion filed its workspace under a random corpus id.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from callobuzz_rag.content_portal.models.workspace import Workspace
from callobuzz_rag.content_portal.routes._access import (
    authorize_document_version,
    authorize_workspace,
    portal_actor,
    scoped_workspace,
)
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.document import Document
from callobuzz_rag.db.models.document_version import DocumentVersion

TENANT_A, TENANT_B = uuid4(), uuid4()
CORPUS_A, CORPUS_B = uuid4(), uuid4()
WS_A, WS_B = uuid4(), uuid4()
VERSION_B, DOC_B = uuid4(), uuid4()


def _session() -> AsyncMock:
    rows = {
        (Workspace, WS_A): SimpleNamespace(id=WS_A, corpus_id=CORPUS_A),
        (Workspace, WS_B): SimpleNamespace(id=WS_B, corpus_id=CORPUS_B),
        (Corpus, CORPUS_A): SimpleNamespace(id=CORPUS_A, tenant_id=TENANT_A),
        (Corpus, CORPUS_B): SimpleNamespace(id=CORPUS_B, tenant_id=TENANT_B),
        (DocumentVersion, VERSION_B): SimpleNamespace(id=VERSION_B, document_id=DOC_B),
        (Document, DOC_B): SimpleNamespace(id=DOC_B, corpus_id=CORPUS_B),
    }

    async def _get(model: object, key: object) -> object | None:
        return rows.get((model, key))

    session = AsyncMock()
    session.get = AsyncMock(side_effect=_get)
    return session


def _request(workspace_id: object = None, **state: object) -> MagicMock:
    request = MagicMock()
    request.path_params = {} if workspace_id is None else {"workspace_id": str(workspace_id)}
    request.state = SimpleNamespace(
        is_admin=state.get("admin", False), tenant=state.get("tenant"), corpus=state.get("corpus")
    )
    return request


CORPUS_KEY_A = {"corpus": SimpleNamespace(id=CORPUS_A, tenant_id=TENANT_A)}
TENANT_KEY_A = {"tenant": SimpleNamespace(id=TENANT_A)}


class TestAuthorizeWorkspace:
    async def test_own_workspace_is_allowed(self) -> None:
        await authorize_workspace(_request(WS_A, **CORPUS_KEY_A), _session())

    async def test_other_clients_workspace_is_refused(self) -> None:
        with pytest.raises(HTTPException) as exc:
            await authorize_workspace(_request(WS_B, **CORPUS_KEY_A), _session())
        assert exc.value.status_code == 403

    async def test_tenant_reaches_only_its_own_corpora(self) -> None:
        await authorize_workspace(_request(WS_A, **TENANT_KEY_A), _session())
        with pytest.raises(HTTPException) as exc:
            await authorize_workspace(_request(WS_B, **TENANT_KEY_A), _session())
        assert exc.value.status_code == 403

    async def test_admin_reaches_any_workspace(self) -> None:
        await authorize_workspace(_request(WS_B, admin=True), _session())

    async def test_unknown_workspace_is_404(self) -> None:
        with pytest.raises(HTTPException) as exc:
            await authorize_workspace(_request(uuid4(), **CORPUS_KEY_A), _session())
        assert exc.value.status_code == 404

    async def test_route_without_workspace_passes_through(self) -> None:
        await authorize_workspace(_request(None, **CORPUS_KEY_A), _session())


class TestFromIngestion:
    async def test_other_clients_document_is_refused(self) -> None:
        with pytest.raises(HTTPException) as exc:
            await authorize_document_version(_request(**CORPUS_KEY_A), _session(), VERSION_B)
        assert exc.value.status_code == 403

    async def test_returns_the_real_owning_corpus(self) -> None:
        corpus_id = await authorize_document_version(_request(admin=True), _session(), VERSION_B)
        assert corpus_id == CORPUS_B

    async def test_unknown_version_is_404(self) -> None:
        with pytest.raises(HTTPException) as exc:
            await authorize_document_version(_request(admin=True), _session(), uuid4())
        assert exc.value.status_code == 404


@pytest.mark.parametrize(
    "module", ["chunks", "review", "ai_chat", "post_ingest", "staging", "workspaces"]
)
def test_every_workspace_router_carries_the_check(module: str) -> None:
    import importlib

    router = importlib.import_module(f"callobuzz_rag.content_portal.routes.{module}").router
    assert any(d.dependency is authorize_workspace for d in router.dependencies)


class TestScopedWorkspace:
    async def test_returns_the_workspace_in_scope(self) -> None:
        workspace = await scoped_workspace(_request(**TENANT_KEY_A), WS_A, _session())
        assert workspace.id == WS_A
        assert workspace.corpus_id == CORPUS_A

    async def test_admin_gets_any_workspace(self) -> None:
        workspace = await scoped_workspace(_request(admin=True), WS_B, _session())
        assert workspace.corpus_id == CORPUS_B

    @pytest.mark.parametrize("state", [CORPUS_KEY_A, TENANT_KEY_A])
    async def test_other_clients_workspace_is_403(self, state: dict[str, object]) -> None:
        with pytest.raises(HTTPException) as exc:
            await scoped_workspace(_request(**state), WS_B, _session())
        assert exc.value.status_code == 403

    async def test_unknown_workspace_is_404(self) -> None:
        with pytest.raises(HTTPException) as exc:
            await scoped_workspace(_request(admin=True), uuid4(), _session())
        assert exc.value.status_code == 404


def _actor_request(user: object = None, headers: dict[str, str] | None = None) -> MagicMock:
    request = MagicMock()
    request.state = SimpleNamespace(user=user) if user is not None else SimpleNamespace()
    request.headers = headers or {}
    return request


class TestPortalActor:
    def test_signed_in_user_overrides_the_body(self) -> None:
        request = _actor_request(user=SimpleNamespace(email="ana@shree.example"))
        assert portal_actor(request, "someone-else") == "ana@shree.example"

    def test_signed_in_user_overrides_the_header(self) -> None:
        request = _actor_request(
            user=SimpleNamespace(email="ana@shree.example"), headers={"X-User-Id": "boss"}
        )
        assert portal_actor(request) == "ana@shree.example"

    def test_key_keeps_the_name_it_sends(self) -> None:
        assert portal_actor(_actor_request(), "integration-user") == "integration-user"

    def test_key_falls_back_to_the_header_then_a_fixed_name(self) -> None:
        assert portal_actor(_actor_request(headers={"X-User-Id": "crm-42"})) == "crm-42"
        assert portal_actor(_actor_request()) == "api_key_user"

    def test_state_without_a_user_attribute_is_a_key(self) -> None:
        request = MagicMock()
        request.state = SimpleNamespace(is_admin=True)
        request.headers = {}
        assert portal_actor(request, "named") == "named"


class TestAddChunkStagedDocument:
    """P4: a chunk may only point at a staged document of its own workspace."""

    def _session(self, staged_document: object) -> AsyncMock:
        from callobuzz_rag.content_portal.models.staged_document import StagedDocument

        async def _get(model: object, key: object) -> object | None:
            if model is StagedDocument:
                return staged_document
            if model is Workspace:
                return SimpleNamespace(id=WS_A, locked_by="ana")
            return None

        session = AsyncMock()
        session.get = AsyncMock(side_effect=_get)
        session.add = MagicMock()
        return session

    async def test_another_workspaces_document_is_refused(self) -> None:
        from callobuzz_rag.content_portal.services.chunk_editor import (
            ChunkEditorService,
            ChunkNotFoundError,
        )

        foreign = SimpleNamespace(id=uuid4(), workspace_id=WS_B)
        session = self._session(foreign)
        with pytest.raises(ChunkNotFoundError, match="Staged document not found"):
            await ChunkEditorService().add_chunk(
                session, WS_A, user="ana", text="x", staged_document_id=foreign.id
            )
        session.add.assert_not_called()

    async def test_unknown_document_is_refused(self) -> None:
        from callobuzz_rag.content_portal.services.chunk_editor import (
            ChunkEditorService,
            ChunkNotFoundError,
        )

        with pytest.raises(ChunkNotFoundError):
            await ChunkEditorService().add_chunk(
                self._session(None), WS_A, user="ana", text="x", staged_document_id=uuid4()
            )

    async def test_route_answers_404(self) -> None:
        from tests.unit.scope_harness import call, scoped_app

        with scoped_app(portal=True) as (app, world):
            from callobuzz_rag.content_portal.models.staged_document import StagedDocument

            other = SimpleNamespace(id=uuid4(), workspace_id=world.workspace_b.id)
            world.add_row(StagedDocument, other)
            res = await call(
                app,
                "admin",
                "POST",
                f"/api/v1/portal/workspaces/{world.workspace_a.id}/chunks",
                json={"text": "x", "user": "ana", "staged_document_id": str(other.id)},
            )
        assert res.status_code == 404, res.text
        assert "Staged document not found" in res.json()["detail"]

    async def test_own_workspaces_document_is_accepted(self) -> None:
        from callobuzz_rag.content_portal.services.chunk_editor import ChunkEditorService

        own = SimpleNamespace(id=uuid4(), workspace_id=WS_A)
        session = self._session(own)
        editor = ChunkEditorService()
        editor._verify_workspace_lock = AsyncMock()  # type: ignore[method-assign]
        editor._get_next_chunk_index = AsyncMock(return_value=0)  # type: ignore[method-assign]
        editor._reindex_chunks = AsyncMock()  # type: ignore[method-assign]
        chunk = await editor.add_chunk(
            session, WS_A, user="ana", text="x", staged_document_id=own.id
        )
        assert chunk.staged_document_id == own.id


class TestFinalizeTargetsTheWorkspacesCorpus:
    """finalize-review used to write every review into a fixed "chunks" collection,
    which on older installs is a real corpus's collection (the pre-multi-corpus
    default), whoever owned the workspace."""

    @pytest.mark.parametrize("key", ["admin", "tk_a", "ck_a"])
    async def test_writes_to_the_corpus_collection(self, key: str, monkeypatch) -> None:
        from callobuzz_rag.content_portal.services import post_ingest_review
        from tests.unit.scope_harness import call, scoped_app

        seen: dict[str, object] = {}

        async def fake_finalize(self, session, workspace_id, *, user, collection_name, **kw):
            seen.update(workspace_id=workspace_id, collection=collection_name, user=user)
            counts = (
                "chunks_in_new_version",
                "chunks_unchanged",
                "chunks_edited",
                "chunks_added",
                "parent_chunks",
                "staged_chunks_rejected",
                "previous_chunks_dropped",
                "staged_chunks_total",
            )
            return {
                "workspace_id": workspace_id,
                "status": "ingested",
                "message": "ok",
                "document_id": uuid4(),
                "previous_version_id": uuid4(),
                "new_version_id": uuid4(),
                **dict.fromkeys(counts, 0),
            }

        monkeypatch.setattr(
            post_ingest_review.PostIngestReviewService, "finalize_review", fake_finalize
        )
        with scoped_app(portal=True) as (app, world):
            res = await call(
                app,
                key,
                "POST",
                f"/api/v1/portal/workspaces/{world.workspace_a.id}/finalize-review",
                json={"user": "ana"},
            )
        assert res.status_code == 200, res.text
        assert seen["collection"] == world.corpus_a.qdrant_collection
        assert seen["workspace_id"] == world.workspace_a.id


def test_no_portal_route_takes_the_corpus_from_the_key_alone() -> None:
    """`get_authenticated_corpus` refuses admin and tenant keys; the portal scopes instead."""
    from pathlib import Path

    import callobuzz_rag.content_portal.routes as routes

    folder = Path(routes.__file__).parent
    offenders = [
        p.name
        for p in folder.glob("*.py")
        if "get_authenticated_corpus" in p.read_text(encoding="utf-8")
    ]
    assert offenders == []


class TestDeletingAWorkspaceRemovesItsFiles:
    async def test_staged_uploads_are_queued_for_deletion(self) -> None:
        from unittest.mock import patch

        from tests.unit.scope_harness import call, scoped_app

        with (
            scoped_app(portal=True) as (app, world),
            patch(
                "callobuzz_rag.storage.cleanup.schedule_prefix_cleanup", new=AsyncMock()
            ) as schedule,
        ):
            ws = world.workspace_a
            res = await call(app, "tk_a", "DELETE", f"/api/v1/portal/workspaces/{ws.id}")
        assert res.status_code == 204, res.text
        schedule.assert_awaited_once()
        assert schedule.call_args.args == (
            app.state.settings.s3_bucket,
            [f"portal/{ws.corpus_id}/{ws.id}/"],
        )

    async def test_a_cleanup_that_cannot_be_queued_does_not_fail_the_delete(self) -> None:
        from unittest.mock import patch

        from tests.unit.scope_harness import call, scoped_app

        with (
            scoped_app(portal=True) as (app, world),
            patch(
                "callobuzz_rag.storage.cleanup.schedule_prefix_cleanup",
                new=AsyncMock(return_value=False),
            ),
        ):
            res = await call(
                app, "admin", "DELETE", f"/api/v1/portal/workspaces/{world.workspace_b.id}"
            )
        assert res.status_code == 204, res.text


class TestChatActionsOnAnotherWorkspacesChunk:
    """A chat action naming a chunk outside the workspace is a 404, like the editor's."""

    @pytest.mark.parametrize(
        "action",
        [
            {
                "tool_name": "rewrite_chunk",
                "arguments": {"chunk_id": str(uuid4()), "new_text": "x"},
            },
            {
                "tool_name": "split_chunk",
                "arguments": {"chunk_id": str(uuid4()), "split_points": [2]},
            },
            {"tool_name": "merge_chunks", "arguments": {"chunk_ids": [str(uuid4()), str(uuid4())]}},
        ],
    )
    async def test_is_not_found(self, action, monkeypatch) -> None:
        from callobuzz_rag.content_portal.routes import ai_chat
        from callobuzz_rag.content_portal.services.ai_chat_service import AIChatService
        from tests.unit.scope_harness import call, scoped_app

        monkeypatch.setattr(
            ai_chat, "_get_chat_service", lambda settings: AIChatService(llm=AsyncMock())
        )
        with scoped_app(portal=True) as (app, world):
            res = await call(
                app,
                "ck_a",
                "POST",
                f"/api/v1/portal/workspaces/{world.workspace_a.id}/chat/execute",
                json={"action": action},
            )
        assert res.status_code == 404, res.text
