"""No stored file outlives its row (L6).

Every path that stores a file used to upload it first and write its row second: a
corpus (or workspace) deleted in between left the file behind with no row pointing at
it, and the delete's prefix cleanup had already run. Now the row is written (flushed)
first — its foreign key takes a share lock on the corpus/workspace row, so a
concurrent delete waits for this transaction and then cleans the file up with the rest,
and a delete that already committed makes the flush fail before anything is stored —
and a commit that fails after the upload deletes the file again.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.usefixtures("unlimited_plan")

GONE = IntegrityError("INSERT", {}, Exception("violates foreign key constraint"))


def _ordered(session: AsyncMock, storage: AsyncMock) -> MagicMock:
    """One mock that records flush, commit, upload and delete in call order."""
    calls = MagicMock()
    calls.attach_mock(session.flush, "flush")
    calls.attach_mock(session.commit, "commit")
    calls.attach_mock(storage.upload, "upload")
    calls.attach_mock(storage.delete, "delete")
    return calls


def _names(calls: MagicMock) -> list[str]:
    return [c[0] for c in calls.mock_calls if c[0] in {"flush", "commit", "upload", "delete"}]


# --- POST /api/v1/ingest ---------------------------------------------------------


def _upload_request() -> tuple[MagicMock, MagicMock, AsyncMock]:
    corpus = SimpleNamespace(id=uuid.uuid4(), qdrant_collection="corpus_x", tenant_id=uuid.uuid4())
    request = MagicMock()
    request.state.corpus = corpus
    request.state.is_admin = False
    request.state.tenant = None
    request.app.state.settings = MagicMock(
        s3_bucket="b", review_expiry_days=7, max_upload_bytes=50 * 1024 * 1024
    )
    file = MagicMock(content_type="text/plain", filename="a.txt")
    file.read = AsyncMock(return_value=b"hello world")
    session = AsyncMock()
    session.get = AsyncMock(return_value=corpus)
    session.add = MagicMock()
    session.refresh = AsyncMock(side_effect=lambda job: setattr(job, "id", uuid.uuid4()))
    return request, file, session


async def _upload(request, file, session, storage) -> object:
    from callobuzz_rag.api.routes.ingest import upload_document

    with (
        patch("callobuzz_rag.api.routes.ingest.object_storage", return_value=storage),
        patch("callobuzz_rag.worker.tasks.ingest_document_task") as task,
    ):
        result = await upload_document(
            request=request,
            file=file,
            corpus_id=None,
            idempotency_key=None,
            mode="auto",
            target_document_id=None,
            auto_confirm=False,
            metadata=None,
            session=session,
        )
    return result, task


async def test_ingest_writes_the_job_row_before_the_file() -> None:
    request, file, session = _upload_request()
    storage = AsyncMock()
    calls = _ordered(session, storage)
    _, task = await _upload(request, file, session, storage)
    assert _names(calls) == ["flush", "upload", "commit"]
    task.delay.assert_called_once()


async def test_ingest_into_a_corpus_deleted_meanwhile_stores_nothing() -> None:
    request, file, session = _upload_request()
    session.flush.side_effect = GONE
    storage = AsyncMock()
    with pytest.raises(HTTPException) as err:
        await _upload(request, file, session, storage)
    assert err.value.status_code == 404
    storage.upload.assert_not_called()


async def test_ingest_removes_the_file_when_the_commit_fails() -> None:
    request, file, session = _upload_request()
    session.commit.side_effect = GONE
    storage = AsyncMock()
    calls = _ordered(session, storage)
    with pytest.raises(HTTPException) as err:
        await _upload(request, file, session, storage)
    assert err.value.status_code == 404
    assert _names(calls) == ["flush", "upload", "commit", "delete"]


async def test_ingest_storage_failure_is_a_503_and_commits_nothing() -> None:
    request, file, session = _upload_request()
    storage = AsyncMock()
    storage.upload.side_effect = ConnectionError("s3 down")
    with pytest.raises(HTTPException) as err:
        await _upload(request, file, session, storage)
    assert err.value.status_code == 503
    session.commit.assert_not_called()


# --- POST /api/v1/portal/workspaces/{id}/content -----------------------------------


def _staging(session: AsyncMock, storage: AsyncMock):
    from callobuzz_rag.content_portal.services.staging_service import StagingService
    from callobuzz_rag.core.config import Settings

    with patch(
        "callobuzz_rag.content_portal.services.staging_service.object_storage",
        return_value=storage,
    ):
        return StagingService(session, Settings(_env_file=None))


@pytest.mark.parametrize("kind", ["text", "url"])
async def test_staging_writes_the_row_before_the_file(kind: str) -> None:
    session = AsyncMock()
    session.add = MagicMock()
    storage = AsyncMock()
    calls = _ordered(session, storage)
    svc = _staging(session, storage)
    upload = svc.upload_text if kind == "text" else svc.upload_url
    await upload(uuid.uuid4(), uuid.uuid4(), "https://example.com" if kind == "url" else "x")
    assert _names(calls)[:2] == ["flush", "upload"]


async def test_staging_file_writes_the_row_before_the_file() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    storage = AsyncMock()
    calls = _ordered(session, storage)
    file = MagicMock(filename="a.pdf", content_type="application/pdf")
    file.read = AsyncMock(return_value=b"%PDF")
    await _staging(session, storage).upload_file(uuid.uuid4(), uuid.uuid4(), file)
    assert _names(calls)[:2] == ["flush", "upload"]


async def test_staging_into_a_workspace_deleted_meanwhile_stores_nothing() -> None:
    from callobuzz_rag.content_portal.services.staging_service import WorkspaceGoneError

    session = AsyncMock()
    session.add = MagicMock()
    session.flush.side_effect = GONE
    storage = AsyncMock()
    with pytest.raises(WorkspaceGoneError):
        await _staging(session, storage).upload_text(uuid.uuid4(), uuid.uuid4(), "x")
    storage.upload.assert_not_called()


async def test_staging_route_removes_the_file_when_the_commit_fails() -> None:
    from tests.unit.scope_harness import call, scoped_app

    storage = AsyncMock()
    with (
        scoped_app(portal=True) as (app, world),
        patch(
            "callobuzz_rag.content_portal.services.staging_service.object_storage",
            return_value=storage,
        ),
    ):
        world.session.commit = AsyncMock(side_effect=GONE)
        ws = world.workspace_a
        res = await call(
            app,
            "ck_a",
            "POST",
            f"/api/v1/portal/workspaces/{ws.id}/content",
            json={"source_type": "text_dump", "content": "hello"},
        )
    assert res.status_code == 404, res.text
    storage.upload.assert_awaited_once()
    key = storage.upload.call_args.args[1]
    storage.delete.assert_awaited_once_with(app.state.settings.s3_bucket, key)


# --- POST /api/v1/portal/workspaces/{id}/ingest ------------------------------------


async def test_portal_ingest_dispatches_only_after_the_commit() -> None:
    """The task used to be queued before the job row was committed: a fast worker found
    no job, made its own with the same id, and the request's commit then collided."""
    from callobuzz_rag.content_portal.services import review_workflow
    from tests.unit.scope_harness import call, scoped_app

    order: list[str] = []
    storage = AsyncMock()
    dispatch = {"storage_key": "corpus/x/y/raw", "job_id": str(uuid.uuid4())}

    async def fake_ingest(self, session, workspace_id, *, user, settings=None):
        return {
            "workspace_id": workspace_id,
            "status": "ingested",
            "message": "queued",
            "chunks_ingested": 1,
            "job_id": uuid.UUID(dispatch["job_id"]),
            "dispatch": dispatch,
        }

    task = MagicMock()
    task.delay.side_effect = lambda **kw: order.append("delay")
    with (
        scoped_app(portal=True) as (app, world),
        patch.object(review_workflow.ReviewWorkflowService, "ingest_workspace", fake_ingest),
        patch("callobuzz_rag.worker.tasks.ingest_document_task", task),
        patch("callobuzz_rag.content_portal.routes.review._storage", return_value=storage),
    ):
        world.session.commit = AsyncMock(side_effect=lambda: order.append("commit"))
        res = await call(
            app, "ck_a", "POST", f"/api/v1/portal/workspaces/{world.workspace_a.id}/ingest"
        )
    assert res.status_code == 200, res.text
    assert order == ["commit", "delay"]
    task.delay.assert_called_once_with(**dispatch)
    assert "dispatch" not in res.json()


async def test_portal_ingest_removes_the_file_when_the_commit_fails() -> None:
    from callobuzz_rag.content_portal.services import review_workflow
    from tests.unit.scope_harness import call, scoped_app

    storage = AsyncMock()

    async def fake_ingest(self, session, workspace_id, *, user, settings=None):
        return {
            "workspace_id": workspace_id,
            "status": "ingested",
            "message": "queued",
            "chunks_ingested": 1,
            "job_id": uuid.uuid4(),
            "dispatch": {"storage_key": "corpus/x/y/raw"},
        }

    task = MagicMock()
    with (
        scoped_app(portal=True) as (app, world),
        patch.object(review_workflow.ReviewWorkflowService, "ingest_workspace", fake_ingest),
        patch("callobuzz_rag.worker.tasks.ingest_document_task", task),
        patch("callobuzz_rag.content_portal.routes.review._storage", return_value=storage),
    ):
        world.session.commit = AsyncMock(side_effect=GONE)
        res = await call(
            app, "ck_a", "POST", f"/api/v1/portal/workspaces/{world.workspace_a.id}/ingest"
        )
    assert res.status_code == 404, res.text
    storage.delete.assert_awaited_once_with(app.state.settings.s3_bucket, "corpus/x/y/raw")
    task.delay.assert_not_called()


async def test_reprocess_never_discards_a_committed_file() -> None:
    """Only a NEW upload's file is taken back out when its commit fails."""
    from callobuzz_rag.content_portal.services import staging_service
    from tests.unit.scope_harness import call, scoped_app

    storage = AsyncMock()
    staged = SimpleNamespace(id=uuid.uuid4(), raw_storage_key="portal/x/y/z/f", ai_status="pending")
    with (
        scoped_app(portal=True) as (app, world),
        patch.object(staging_service, "object_storage", return_value=storage),
        patch.object(
            staging_service.StagingService, "reprocess_content", AsyncMock(return_value=staged)
        ),
    ):
        world.session.commit = AsyncMock(side_effect=GONE)
        ws = world.workspace_a
        res = await call(
            app, "ck_a", "POST", f"/api/v1/portal/workspaces/{ws.id}/content/{staged.id}/reprocess"
        )
    assert res.status_code == 503, res.text
    storage.delete.assert_not_called()


async def test_reprocess_of_another_workspaces_content_is_404() -> None:
    from tests.unit.scope_harness import call, scoped_app

    with (
        scoped_app(portal=True) as (app, world),
        patch(
            "callobuzz_rag.content_portal.services.staging_service.object_storage",
            return_value=AsyncMock(),
        ),
    ):
        ws = world.workspace_a
        res = await call(
            app,
            "ck_a",
            "POST",
            f"/api/v1/portal/workspaces/{ws.id}/content/{uuid.uuid4()}/reprocess",
        )
    assert res.status_code == 404, res.text
