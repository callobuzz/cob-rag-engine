"""GET /documents?source=website|articles|files narrows the list by where a document came from."""

import uuid
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.dialects import postgresql

from callobuzz_rag.api.routes.documents import list_documents


async def _sql(source: str | None) -> str:
    session = AsyncMock()
    rows = MagicMock()
    rows.scalars.return_value.all.return_value = []
    count = MagicMock()
    count.scalar.return_value = 0
    session.execute = AsyncMock(side_effect=[rows, count])
    corpus = MagicMock(id=uuid.uuid4())
    await list_documents(
        request=MagicMock(), search=None, source=source, limit=50, offset=0,
        session=session, corpus=corpus,
    )  # fmt: skip
    stmt = session.execute.await_args_list[0].args[0]
    return str(stmt.compile(dialect=postgresql.dialect()))


async def test_no_source_adds_no_condition() -> None:
    sql = await _sql(None)
    assert "LIKE" not in sql and "IS NULL" not in sql


async def test_website_selects_web_ids() -> None:
    sql = await _sql("website")
    assert "documents.external_id LIKE" in sql


async def test_articles_selects_article_and_faq_ids() -> None:
    sql = await _sql("articles")
    assert sql.count("documents.external_id LIKE") == 2 and " OR " in sql


async def test_files_selects_documents_without_an_external_id() -> None:
    sql = await _sql("files")
    assert "documents.external_id IS NULL" in sql


def test_source_is_a_documented_query_parameter() -> None:
    from callobuzz_rag.api.app import create_app

    params = create_app().openapi()["paths"]["/api/v1/documents"]["get"]["parameters"]
    source = next(p for p in params if p["name"] == "source")
    options = {o for part in source["schema"]["anyOf"] for o in part.get("enum", [])}
    assert options == {"website", "articles", "files"}  # anything else is a 422
