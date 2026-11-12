"""Articles and FAQ entries (BIZ3): written text kept in knowledge_articles, learned as records."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.routes.articles import create_article, get_article, update_article
from callobuzz_rag.api.schemas.articles import ArticleRequest
from callobuzz_rag.db.models.document import Document
from callobuzz_rag.db.models.ingest_job import IngestJob
from callobuzz_rag.db.models.knowledge_article import KnowledgeArticle

pytestmark = pytest.mark.usefixtures("unlimited_plan")

NOW = datetime(2026, 10, 5, tzinfo=UTC)
BODY = "## Cost\nRoot canal starts at ₹4,500 per tooth.\n\n## Time\nUsually two visits."


def _corpus() -> Any:
    return SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), qdrant_collection="corpus_c1")


def _request(email: str | None = "owner@shreedental.in") -> Any:
    request = MagicMock()
    request.method = "POST"
    request.state.user = SimpleNamespace(email=email) if email else None
    return request


def _session() -> MagicMock:
    session = MagicMock()
    session.new = set()
    session.add = MagicMock(side_effect=lambda obj: session.new.add(obj))

    async def commit() -> None:
        session.new.clear()

    session.commit = AsyncMock(side_effect=commit)
    session.rollback = AsyncMock()
    session.delete = AsyncMock()
    session.expunge = MagicMock()
    job_id = uuid.uuid4()

    async def refresh(obj: Any) -> None:
        if isinstance(obj, IngestJob):
            obj.id = job_id
        else:
            obj.created_at = obj.updated_at = NOW

    session.refresh = AsyncMock(side_effect=refresh)
    session.job_id = job_id
    return session


def _row(corpus: Any, *, kind: str = "article", title: str = "Root canal treatment") -> Any:
    ident = uuid.uuid4()
    return KnowledgeArticle(
        id=ident,
        corpus_id=corpus.id,
        tenant_id=corpus.tenant_id,
        external_id=f"{kind}:{ident}",
        kind=kind,
        title=title,
        body=BODY,
        created_by="owner@shreedental.in",
        created_at=NOW,
        updated_at=NOW,
    )


def _found(value: Any) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.first.return_value = value
    return result


async def test_create_article_stores_the_text_and_queues_one_markdown_record() -> None:
    session, corpus = _session(), _corpus()
    body = ArticleRequest(kind="article", title="Root canal treatment", body=BODY)
    with patch("callobuzz_rag.worker.tasks.ingest_records_task") as task:
        out = await create_article(body, _request(), session, corpus)  # type: ignore[arg-type]
    row = session.add.call_args_list[0].args[0]
    assert isinstance(row, KnowledgeArticle) and row.external_id == f"article:{row.id}"
    assert row.tenant_id == corpus.tenant_id and row.created_by == "owner@shreedental.in"
    job = session.add.call_args_list[1].args[0]
    assert isinstance(job, IngestJob) and job.mode == "records" and job.total_documents == 1
    records = task.delay.call_args.kwargs["records"]
    assert len(records) == 1
    assert records[0]["id"] == row.external_id
    assert records[0]["title"] == "Root canal treatment"
    assert "## Cost" in records[0]["text"] and "₹4,500" in records[0]["text"]
    assert records[0]["markdown"] is True
    assert records[0]["metadata"] == {"source": "articles", "kind": "article"}
    assert task.delay.call_args.kwargs["corpus_id"] == str(corpus.id)
    assert out.id == row.id and out.status == "queued" and out.document_id is None
    assert out.job_id == session.job_id


async def test_create_faq_uses_the_question_as_the_title() -> None:
    session = _session()
    body = ArticleRequest(kind="faq", title="Do you open on Sunday?", body="Yes, 10 am to 2 pm.")
    with patch("callobuzz_rag.worker.tasks.ingest_records_task") as task:
        out = await create_article(body, _request(None), session, _corpus())  # type: ignore[arg-type]
    row = session.add.call_args_list[0].args[0]
    record = task.delay.call_args.kwargs["records"][0]
    assert record["id"] == f"faq:{row.id}"
    assert record["title"] == "Do you open on Sunday?" and record["text"] == "Yes, 10 am to 2 pm."
    assert out.kind == "faq" and row.created_by == "api"


async def test_a_full_plan_refuses_and_leaves_no_row() -> None:
    session = _session()
    refusal = APIError(409, "Document limit reached (50)", "document_limit_reached")
    body = ArticleRequest(kind="article", title="Fees", body="Consultation ₹500")
    with (
        patch(
            "callobuzz_rag.api.routes.articles.check_document_room", AsyncMock(side_effect=refusal)
        ),
        pytest.raises(APIError) as err,
    ):
        await create_article(body, _request(), session, _corpus())  # type: ignore[arg-type]
    assert err.value.code == "document_limit_reached"
    row = session.add.call_args_list[0].args[0]
    session.expunge.assert_called_once_with(row)  # the uncommitted row is dropped
    assert session.add.call_count == 1  # no job
    session.commit.assert_not_awaited()


async def test_queue_down_removes_the_stored_article() -> None:
    session = _session()
    body = ArticleRequest(kind="article", title="Fees", body="Consultation ₹500")
    with patch("callobuzz_rag.worker.tasks.ingest_records_task") as task:
        task.delay.side_effect = RuntimeError("broker gone")
        with pytest.raises(HTTPException) as err:
            await create_article(body, _request(), session, _corpus())  # type: ignore[arg-type]
    assert err.value.status_code == 503
    row = session.add.call_args_list[0].args[0]
    session.delete.assert_awaited_once_with(row)


def test_body_over_fifty_thousand_characters_is_refused() -> None:
    with pytest.raises(ValidationError):
        ArticleRequest(kind="article", title="Big", body="x" * 50_001)
    assert ArticleRequest(kind="article", title="Big", body="x" * 50_000).body


@pytest.mark.parametrize("bad", [{"title": "  "}, {"body": "\n"}, {"kind": "page"}])
def test_blank_or_unknown_input_is_refused(bad: dict[str, str]) -> None:
    fields = {"kind": "article", "title": "Fees", "body": "x", **bad}
    with pytest.raises(ValidationError):
        ArticleRequest(**fields)  # type: ignore[arg-type]


def test_unknown_fields_are_refused() -> None:
    with pytest.raises(ValidationError):
        ArticleRequest(kind="article", title="A", body="b", corpus_id="x")  # type: ignore[call-arg]


async def test_get_returns_the_text_as_written_by_article_or_document_id() -> None:
    corpus = _corpus()
    row = _row(corpus)
    doc_id = uuid.uuid4()
    session = _session()
    session.get = AsyncMock(return_value=row)
    session.execute = AsyncMock(return_value=_found(doc_id))
    out = await get_article(row.id, session, corpus)  # type: ignore[arg-type]
    assert out.body == BODY and out.status == "ready" and out.document_id == doc_id

    # by the id of the document it became
    doc = SimpleNamespace(id=doc_id, corpus_id=corpus.id, external_id=row.external_id)
    session2 = _session()

    async def get(model: Any, ident: Any) -> Any:
        return doc if model is Document else None

    session2.get = AsyncMock(side_effect=get)
    session2.execute = AsyncMock(side_effect=[_found(row), _found(doc_id)])
    out2 = await get_article(doc_id, session2, corpus)  # type: ignore[arg-type]
    assert out2.id == row.id and out2.title == "Root canal treatment"


async def test_an_article_of_another_knowledge_base_is_404() -> None:
    corpus = _corpus()
    stranger = _row(_corpus())  # belongs to a different knowledge base
    session = _session()

    async def get(model: Any, ident: Any) -> Any:
        return stranger if model is KnowledgeArticle else None

    session.get = AsyncMock(side_effect=get)
    with pytest.raises(APIError) as err:
        await get_article(stranger.id, session, corpus)  # type: ignore[arg-type]
    assert err.value.status_code == 404 and err.value.code == "article_not_found"


async def test_a_document_that_is_not_an_article_is_404() -> None:
    corpus = _corpus()
    doc = SimpleNamespace(id=uuid.uuid4(), corpus_id=corpus.id, external_id="web:https://x.in/")
    session = _session()

    async def get(model: Any, ident: Any) -> Any:
        return doc if model is Document else None

    session.get = AsyncMock(side_effect=get)
    session.execute = AsyncMock(return_value=_found(None))
    with pytest.raises(APIError) as err:
        await get_article(doc.id, session, corpus)  # type: ignore[arg-type]
    assert err.value.code == "article_not_found"

    file_doc = SimpleNamespace(id=uuid.uuid4(), corpus_id=corpus.id, external_id=None)
    session.get = AsyncMock(side_effect=lambda m, i: file_doc if m is Document else None)
    with pytest.raises(APIError):
        await get_article(file_doc.id, session, corpus)  # type: ignore[arg-type]


async def test_put_learns_a_new_version_under_the_same_id() -> None:
    corpus = _corpus()
    row = _row(corpus)
    external_id = row.external_id
    session = _session()
    session.get = AsyncMock(return_value=row)
    session.execute = AsyncMock(return_value=_found(uuid.uuid4()))
    body = ArticleRequest(kind="article", title="Root canal: new price", body="From ₹5,000.")
    with patch("callobuzz_rag.worker.tasks.ingest_records_task") as task:
        out = await update_article(row.id, body, _request(), session, corpus)  # type: ignore[arg-type]
    record = task.delay.call_args.kwargs["records"][0]
    assert record["id"] == external_id  # same id: the pipeline stores a new version
    assert record["title"] == "Root canal: new price" and record["text"] == "From ₹5,000."
    assert row.title == "Root canal: new price" and row.body == "From ₹5,000."
    assert row.external_id == external_id
    assert out.job_id == session.job_id


async def test_put_cannot_change_the_kind() -> None:
    corpus = _corpus()
    row = _row(corpus, kind="faq", title="Do you open on Sunday?")
    session = _session()
    session.get = AsyncMock(return_value=row)
    body = ArticleRequest(kind="article", title="Sunday", body="Open")
    with (
        patch("callobuzz_rag.worker.tasks.ingest_records_task") as task,
        pytest.raises(APIError) as err,
    ):
        await update_article(row.id, body, _request(), session, corpus)  # type: ignore[arg-type]
    assert err.value.status_code == 422 and err.value.code == "article_kind_changed"
    task.delay.assert_not_called()


async def test_deleting_an_article_document_deletes_its_row() -> None:
    from callobuzz_rag.api.routes.documents import delete_document

    corpus = SimpleNamespace(id=uuid.uuid4(), qdrant_collection="corpus_c1")
    doc = SimpleNamespace(
        id=uuid.uuid4(),
        external_id="article:1f2e",
        corpus_id=corpus.id,
        versions=[],
        doc_metadata={},
        file_name="article:1f2e",
    )
    statements: list[Any] = []

    async def execute(stmt: Any) -> Any:
        statements.append(stmt)
        result = MagicMock()
        result.scalar_one_or_none.return_value = doc
        return result

    session = MagicMock()
    session.execute = AsyncMock(side_effect=execute)
    session.commit = AsyncMock()
    session.delete = AsyncMock()
    request = MagicMock()
    request.app.state.vector_store.delete_by_filter = AsyncMock()
    with (
        patch(
            "callobuzz_rag.migration.context.get_dual_write_collection",
            AsyncMock(return_value=None),
        ),
        patch("callobuzz_rag.api.services.delete_guard.needs_confirm_name", return_value=False),
    ):
        await delete_document(request, doc.id, session, corpus, None, None)  # type: ignore[arg-type]
    deletes = [s for s in statements if getattr(s, "__visit_name__", "") == "delete"]
    assert len(deletes) == 1 and deletes[0].table.name == "knowledge_articles"
    session.commit.assert_awaited()


# --------------------------------------------- restore and replace keep the editor honest


def _written_doc(external_id: str) -> Any:
    return SimpleNamespace(id=uuid.uuid4(), external_id=external_id, versions=[], metadata_={})


def _results(*docs: Any) -> MagicMock:
    """A session whose lock query returns nothing and whose next queries return `docs`."""
    session = MagicMock()
    rows = [MagicMock()]
    for doc in docs:
        row = MagicMock()
        row.scalar_one_or_none.return_value = doc
        rows.append(row)
    session.execute = AsyncMock(side_effect=rows)
    return session


@pytest.mark.parametrize("external_id", [f"article:{uuid.uuid4()}", f"faq:{uuid.uuid4()}"])
async def test_restoring_a_version_of_written_text_is_refused(external_id: str) -> None:
    from callobuzz_rag.api.routes.documents import restore_version

    doc = _written_doc(external_id)
    session = MagicMock()
    row = MagicMock()
    row.scalar_one_or_none.return_value = doc
    session.execute = AsyncMock(return_value=row)
    with pytest.raises(APIError) as exc:
        await restore_version(
            _request(), doc.id, uuid.uuid4(), session=session, corpus=_corpus(), _owner=None
        )
    assert exc.value.status_code == 409 and exc.value.code == "article_edit_instead"
    assert "Knowledge" in exc.value.detail


@pytest.mark.parametrize("which", ["old", "new"])
async def test_replacing_written_text_is_refused(which: str) -> None:
    from callobuzz_rag.api.routes.documents import merge_documents

    written, plain = _written_doc(f"article:{uuid.uuid4()}"), _written_doc("menu.pdf")
    old, new = (written, plain) if which == "old" else (plain, written)
    with pytest.raises(APIError) as exc:
        await merge_documents(
            _request(), new.id, old.id, session=_results(old, new), corpus=_corpus(), _owner=None
        )
    assert exc.value.status_code == 409 and exc.value.code == "article_edit_instead"
