"""The active-document limit on every path that can add a document (accounts A6).

Each route is called directly with a tenant at its cap (limit 5, 5 active documents). A
call that would add documents gets 409 `document_limit_reached` with `adding`; a call that
adds none (updating existing content) passes. The route's own `check_document_room` is
wrapped so that passing the check stops the route there (`Passed`): what the route does
next (storage, the task queue, re-embedding) is covered by its own tests.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from callobuzz_rag.accounts import caps
from callobuzz_rag.api.errors import APIError

LIMIT = 5


class Passed(Exception):  # noqa: N818 — a test signal, not an error
    """The cap check let the call through."""


class Seen(list[Any]):
    """The `adds` values the route passed; `.returned` what the check returned for each."""

    def __init__(self) -> None:
        super().__init__()
        self.returned: list[int | None] = []


@contextmanager
def at_cap(target: str, *, used: int = LIMIT, queued: int = 0) -> Iterator[Seen]:
    """The tenant's plan allows LIMIT active documents; it has `used` and its in-flight
    jobs will add `queued`. Patches the check where `target` looks it up."""
    real = caps.check_document_room
    seen = Seen()

    async def spy(session: Any, tenant_id: Any, adds: Any) -> None:
        seen.append(adds)
        seen.returned.append(await real(session, tenant_id, adds))
        raise Passed

    with (
        patch.object(
            caps, "effective_limits", AsyncMock(return_value=caps.Limits(max_documents=LIMIT))
        ),
        patch.object(caps, "lock_tenant_documents", AsyncMock()),
        patch.object(caps, "document_usage", AsyncMock(return_value=(used, queued))),
        patch(target, spy),
    ):
        yield seen


def _refused(err: pytest.ExceptionInfo[APIError], adding: int) -> None:
    assert err.value.status_code == 409
    assert err.value.code == "document_limit_reached"
    assert err.value.detail == f"Document limit reached ({LIMIT})"
    assert err.value.extra == {"limit": LIMIT, "used": LIMIT, "queued": 0, "adding": adding}


def _corpus() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), qdrant_collection="c")


def _admin_request() -> MagicMock:
    request = MagicMock()
    request.state.corpus = None
    request.state.tenant = None
    request.state.is_admin = True
    request.app.state.settings.max_upload_bytes = 50 * 1024 * 1024
    return request


# ------------------------------------------------------------ 1. upload


async def _upload(mode: str, target: uuid.UUID | None, *, target_exists: bool) -> None:
    from callobuzz_rag.api.routes.ingest import upload_document

    corpus = _corpus()
    session = AsyncMock()
    session.get = AsyncMock(return_value=corpus)
    found = MagicMock()
    found.first.return_value = (target,) if target_exists else None
    session.execute = AsyncMock(return_value=found)
    file = MagicMock(content_type="text/plain", filename="a.txt")
    file.read = AsyncMock(return_value=b"hello")
    await upload_document(
        request=_admin_request(),
        file=file,
        corpus_id=corpus.id,
        idempotency_key=None,
        mode=mode,
        target_document_id=target,
        auto_confirm=False,
        metadata=None,
        session=session,
    )


@pytest.mark.parametrize("mode", ["new", "auto"])
async def test_upload_that_may_add_a_document_is_refused(mode: str) -> None:
    with (
        at_cap("callobuzz_rag.api.routes.ingest.check_document_room"),
        pytest.raises(APIError) as err,
    ):
        await _upload(mode, None, target_exists=False)
    _refused(err, 1)


@pytest.mark.parametrize("mode", ["replace", "version"])
async def test_upload_of_an_existing_document_is_allowed(mode: str) -> None:
    with (
        at_cap("callobuzz_rag.api.routes.ingest.check_document_room") as seen,
        pytest.raises(Passed),
    ):
        await _upload(mode, uuid.uuid4(), target_exists=True)
    assert seen.returned == [0]  # the job reserves nothing


async def test_upload_replacing_an_unknown_document_counts_as_new() -> None:
    with (
        at_cap("callobuzz_rag.api.routes.ingest.check_document_room"),
        pytest.raises(APIError) as err,
    ):
        await _upload("replace", uuid.uuid4(), target_exists=False)
    _refused(err, 1)


async def test_upload_under_the_cap_is_allowed() -> None:
    target = "callobuzz_rag.api.routes.ingest.check_document_room"
    with at_cap(target, used=LIMIT - 1), pytest.raises(Passed):
        await _upload("new", None, target_exists=False)


# ----------------------------------------------------------- 2. records


async def _records(session: AsyncMock, ids: list[str], known: int) -> None:
    from callobuzz_rag.api.routes.ingest import ingest_records
    from callobuzz_rag.api.schemas.ingest import IngestRecordsRequest

    corpus = _corpus()
    session.get = AsyncMock(return_value=corpus)
    count = MagicMock()
    count.scalar.return_value = known
    session.execute = AsyncMock(return_value=count)
    body = IngestRecordsRequest(
        corpus_id=corpus.id, records=[{"id": i, "text": f"record {i}"} for i in ids]
    )
    await ingest_records(_admin_request(), body, session)


async def test_records_adding_new_ids_is_refused_with_the_count() -> None:
    session = AsyncMock()
    with (
        at_cap("callobuzz_rag.api.routes.ingest.check_document_room"),
        pytest.raises(APIError) as err,
    ):
        await _records(session, ["a", "b", "c"], known=1)  # two are new
    _refused(err, 2)
    stmt = session.execute.await_args.args[0]
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "count(DISTINCT documents.external_id)" in sql
    assert "documents.external_id IN" in sql and "documents.corpus_id = " in sql


async def test_records_reimport_of_existing_ids_is_allowed() -> None:
    with (
        at_cap("callobuzz_rag.api.routes.ingest.check_document_room") as seen,
        pytest.raises(Passed),
    ):
        await _records(AsyncMock(), ["a", "b"], known=2)
    assert seen.returned == [0]


# ----------------------------------------------------------- 3. confirm


async def _confirm(action: str) -> MagicMock:
    from callobuzz_rag.api.routes.ingest import confirm_pending_review
    from callobuzz_rag.api.schemas.ingest import ConfirmReviewRequest

    corpus = _corpus()
    job = SimpleNamespace(
        id=uuid.uuid4(),
        corpus_id=corpus.id,
        status="pending_review",
        expires_at=datetime.now(UTC) + timedelta(days=1),
        mode="auto",
        target_document_id=None,
        pending_new_documents=1,
        storage_key="raw/x",
        review_metadata={},
    )
    session = AsyncMock()
    found = MagicMock()
    found.scalar_one_or_none.return_value = job
    session.execute = AsyncMock(return_value=found)
    session.get = AsyncMock(return_value=corpus)
    task = MagicMock()
    body = ConfirmReviewRequest(
        action=action, target_document_id=uuid.uuid4() if action == "replace" else None
    )
    with patch("callobuzz_rag.worker.tasks.ingest_document_task", task):
        await confirm_pending_review(_admin_request(), job.id, body, session)
    task.job = job
    return task


async def test_confirm_as_new_is_refused() -> None:
    with (
        at_cap("callobuzz_rag.api.routes.ingest.check_document_room") as seen,
        pytest.raises(APIError) as err,
    ):
        await _confirm("new")
    _refused(err, 1)
    assert seen == [1]


async def test_confirm_as_replace_is_allowed_and_never_checks() -> None:
    with at_cap("callobuzz_rag.api.routes.ingest.check_document_room") as seen:
        task = await _confirm("replace")
    assert seen == []
    task.delay.assert_called_once()
    assert task.job.pending_new_documents == 0  # the queued job reserves nothing


# ----------------------------------------------------------- 4. restore


async def _restore(*, has_active: bool) -> None:
    from callobuzz_rag.api.routes.documents import restore_version

    corpus = _corpus()
    old = SimpleNamespace(id=uuid.uuid4(), status="inactive")
    versions = [old]
    if has_active:
        versions.append(SimpleNamespace(id=uuid.uuid4(), status="active"))
    doc = SimpleNamespace(id=uuid.uuid4(), external_id=None, versions=versions)
    session = AsyncMock()
    found = MagicMock()
    found.scalar_one_or_none.return_value = doc
    session.execute = AsyncMock(return_value=found)
    await restore_version(MagicMock(), doc.id, old.id, session, corpus)  # type: ignore[arg-type]


async def test_restoring_a_document_with_no_active_version_is_refused() -> None:
    with (
        at_cap("callobuzz_rag.api.routes.documents.check_document_room"),
        pytest.raises(APIError) as err,
    ):
        await _restore(has_active=False)
    _refused(err, 1)


async def test_switching_versions_of_an_active_document_is_allowed() -> None:
    with (
        at_cap("callobuzz_rag.api.routes.documents.check_document_room") as seen,
        pytest.raises(Passed),
    ):
        await _restore(has_active=True)
    assert seen == [0]


# ------------------------------------------------------- 5. portal ingest


async def test_portal_ingest_is_refused_at_the_cap() -> None:
    from callobuzz_rag.content_portal.routes.review import ingest_workspace

    corpus = _corpus()
    workspace = SimpleNamespace(id=uuid.uuid4(), corpus_id=corpus.id)
    session = AsyncMock()
    session.get = AsyncMock(return_value=corpus)
    service = AsyncMock()
    with (
        at_cap("callobuzz_rag.accounts.caps.check_document_room") as seen,
        patch("callobuzz_rag.content_portal.routes.review._service", service),
        pytest.raises(APIError) as err,
    ):
        await ingest_workspace(
            workspace.id,
            MagicMock(),
            None,
            session,
            MagicMock(),
            workspace,  # type: ignore[arg-type]
        )
    _refused(err, 1)
    assert seen == [1]
    service.ingest_workspace.assert_not_awaited()  # nothing packaged, stored or queued


# ------------------------------------------------------------- the count


async def test_active_document_count_query_shape() -> None:
    """One query per tenant across all its knowledge bases, active versions only (the
    semantics run on PostgreSQL in tests/integration/test_caps_flow.py)."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar.return_value = 3
    session.execute = AsyncMock(return_value=result)
    assert await caps.active_document_count(session, uuid.uuid4()) == 3
    stmt = session.execute.await_args.args[0]
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "count(DISTINCT documents.id)" in sql
    assert "JOIN corpora ON corpora.id = documents.corpus_id" in sql
    assert "corpora.tenant_id = " in sql
    assert "document_versions.status = " in sql and "EXISTS" in sql


async def test_nothing_to_add_never_queries() -> None:
    limits = AsyncMock()
    with patch.object(caps, "effective_limits", limits):
        await caps.check_document_room(AsyncMock(), uuid.uuid4(), 0)
    limits.assert_not_awaited()


async def test_an_unlimited_plan_never_computes_adds() -> None:
    adds = AsyncMock(return_value=1)
    with patch.object(caps, "effective_limits", AsyncMock(return_value=caps.Limits())):
        await caps.check_document_room(AsyncMock(), uuid.uuid4(), adds)
    adds.assert_not_awaited()


async def test_portal_ingest_refused_for_storage_tells_the_owner() -> None:
    """FU-4 / FU-6: the portal's packaged document is an upload; when the day's uploads or the
    storage are used it is refused (409/429 from the service) and the alert is scheduled."""
    from callobuzz_rag.content_portal.routes.review import ingest_workspace

    corpus = _corpus()
    workspace = SimpleNamespace(id=uuid.uuid4(), corpus_id=corpus.id)
    session = AsyncMock()
    session.get = AsyncMock(return_value=corpus)
    service = AsyncMock()
    service.ingest_workspace = AsyncMock(side_effect=APIError(409, "full", "storage_limit_reached"))
    request = MagicMock()
    with (
        patch("callobuzz_rag.accounts.caps.check_document_room", AsyncMock(return_value=1)),
        patch("callobuzz_rag.content_portal.routes.review._service", service),
        patch("callobuzz_rag.content_portal.routes.review.alerts.schedule_upload_alerts") as alert,
        pytest.raises(APIError) as err,
    ):
        await ingest_workspace(
            workspace.id,
            request,
            None,
            session,
            MagicMock(),
            workspace,  # type: ignore[arg-type]
        )
    assert err.value.code == "storage_limit_reached"
    alert.assert_called_once_with(request.app, corpus.tenant_id)
