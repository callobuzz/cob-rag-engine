"""GET /documents/{id} with chunks; list search, external_id and metadata (G-11)."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from tests.unit.scope_harness import MATRIX, call, check, scoped_app

T0 = datetime(2026, 9, 1, tzinfo=UTC)


def _result(*, one=None, rows=None, scalar=None, scalars=None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = one
    result.all.return_value = rows or []
    result.scalar.return_value = scalar
    result.scalars.return_value.all.return_value = scalars or []
    return result


def _doc(corpus_id: uuid.UUID) -> SimpleNamespace:
    active = SimpleNamespace(
        id=uuid.uuid4(),
        status="active",
        parser_version="p",
        chunk_schema_version="s",
        created_at=T0.replace(day=2),
    )
    old = SimpleNamespace(
        id=uuid.uuid4(),
        status="inactive",
        parser_version="p",
        chunk_schema_version="s",
        created_at=T0,
    )
    return SimpleNamespace(
        id=uuid.uuid4(),
        corpus_id=corpus_id,
        file_name="price-list.pdf",
        mime_type="application/pdf",
        size_bytes=1234,
        ingested_at=T0,
        external_id="sku-9",
        doc_metadata={"category": "pricing"},
        source_uri=None,
        versions=[old, active],
    )


def _chunk(i: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        chunk_index=i,
        text=f"chunk {i}",
        structure_path=["Prices", "Home collection"],
        page=1,
        length_tokens=12,
        is_table=False,
        is_code=False,
        parent_chunk_id=None,
    )


@pytest.mark.parametrize(("key", "which", "outcome"), MATRIX)
async def test_detail_scoping(key, which, outcome) -> None:
    with scoped_app() as (app, world):
        res = await call(
            app, key, "GET", f"/api/v1/documents/{uuid.uuid4()}?corpus_id={world.corpus(which).id}"
        )
    check(outcome, res.status_code, res.text)


async def test_detail_shows_the_active_versions_chunks() -> None:
    with scoped_app() as (app, world):
        doc = _doc(world.corpus_a.id)
        active, old = doc.versions[1], doc.versions[0]
        world.session.execute = AsyncMock(
            side_effect=[
                _result(one=doc),
                _result(rows=[(active.id, 3), (old.id, 2)]),
                _result(scalars=[_chunk(0), _chunk(1)]),
            ]
        )
        res = await call(
            app,
            "tk_a",
            "GET",
            f"/api/v1/documents/{doc.id}?corpus_id={world.corpus_a.id}&chunk_limit=2",
        )
        doc_sql = str(world.session.execute.await_args_list[0].args[0])
        chunk_sql = str(
            world.session.execute.await_args_list[2].args[0].compile(dialect=postgresql.dialect())
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["external_id"] == "sku-9" and body["metadata"] == {"category": "pricing"}
    assert body["active_version_id"] == body["version_id"] == str(active.id)
    assert [v["chunk_count"] for v in body["versions"]] == [3, 2]  # newest first
    assert (body["chunk_total"], body["chunk_offset"], body["chunk_limit"]) == (3, 0, 2)
    assert body["chunks"][0]["section"] == ["Prices", "Home collection"]
    assert body["chunks"][1]["text"] == "chunk 1"
    assert "documents.corpus_id" in doc_sql  # a document is looked up inside the corpus only
    assert "LIMIT" in chunk_sql and "OFFSET" in chunk_sql


async def test_detail_can_show_an_old_version() -> None:
    with scoped_app() as (app, world):
        doc = _doc(world.corpus_a.id)
        old = doc.versions[0]
        world.session.execute = AsyncMock(
            side_effect=[_result(one=doc), _result(rows=[(old.id, 2)]), _result(scalars=[])]
        )
        res = await call(app, "ck_a", "GET", f"/api/v1/documents/{doc.id}?version_id={old.id}")
    assert res.status_code == 200
    assert res.json()["version_id"] == str(old.id) and res.json()["chunk_total"] == 2


async def test_unknown_version_is_404() -> None:
    with scoped_app() as (app, world):
        doc = _doc(world.corpus_a.id)
        world.session.execute = AsyncMock(return_value=_result(one=doc))
        res = await call(
            app, "ck_a", "GET", f"/api/v1/documents/{doc.id}?version_id={uuid.uuid4()}"
        )
    assert res.status_code == 404


async def test_list_searches_and_carries_external_id_and_metadata() -> None:
    with scoped_app() as (app, world):
        doc = _doc(world.corpus_a.id)
        world.session.execute = AsyncMock(side_effect=[_result(scalars=[doc]), _result(scalar=1)])
        res = await call(app, "ck_a", "GET", "/api/v1/documents?search=50%25_off")
        stmt = world.session.execute.await_args_list[0].args[0]
    assert res.status_code == 200, res.text
    item = res.json()["items"][0]
    assert item["external_id"] == "sku-9" and item["metadata"] == {"category": "pricing"}
    compiled = stmt.compile(dialect=postgresql.dialect())
    sql = str(compiled).lower()
    assert "documents.file_name ilike" in sql and "documents.external_id ilike" in sql
    # % and _ in the search text are literal, not wildcards.
    assert "%50\\%\\_off%" in compiled.params.values()


async def test_versions_are_counted_not_loaded() -> None:
    with scoped_app() as (app, world):
        doc = _doc(world.corpus_a.id)
        world.session.execute = AsyncMock(
            side_effect=[_result(one=doc), _result(rows=[(doc.versions[1].id, 7)])]
        )
        res = await call(app, "ck_a", "GET", f"/api/v1/documents/{doc.id}/versions")
        count_sql = str(world.session.execute.await_args_list[1].args[0])
    assert res.status_code == 200
    assert [v["chunk_count"] for v in res.json()["versions"]] == [7, 0]
    assert "count(chunks.id)" in count_sql and "GROUP BY" in count_sql
