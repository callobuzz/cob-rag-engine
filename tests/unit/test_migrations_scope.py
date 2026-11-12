"""Migrations: listed across corpora, readable by the owning tenant, acted on by admin (G-10)."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from callobuzz_rag.db.models.corpus_migration import CorpusMigration
from tests.unit.scope_harness import call, scoped_app


def _migration(corpus_id: uuid.UUID) -> CorpusMigration:
    return CorpusMigration(
        id=uuid.uuid4(),
        corpus_id=corpus_id,
        strategy="re_embed",
        status="pending_approval",
        from_config={"model": "a"},
        to_config={"model": "b"},
        old_collection="c_v0",
        new_collection="c_v1",
        chunks_total=0,
        chunks_done=0,
        memories_total=0,
        memories_done=0,
        created_at=datetime(2026, 9, 30, tzinfo=UTC),
    )


def _result(*, scalar=None, scalars=None) -> MagicMock:
    result = MagicMock()
    result.scalar.return_value = scalar
    result.scalars.return_value.all.return_value = scalars or []
    return result


LIST_MATRIX = [
    ("admin", "b", 200),
    ("tk_a", "a", 200),
    ("tk_a", "b", 403),
    ("ck_a", "a", 403),  # migrations are not a client screen
    (None, "a", 401),
]


@pytest.mark.parametrize(("key", "which", "status"), LIST_MATRIX)
@pytest.mark.parametrize(
    "path", ["/api/v1/migrations?corpus_id={c}", "/api/v1/corpora/{c}/migrations"]
)
async def test_list_scoping(path, key, which, status) -> None:
    with scoped_app() as (app, world):
        res = await call(app, key, "GET", path.format(c=world.corpus(which).id))
    assert res.status_code == status, res.text


async def test_list_across_corpora_with_filters_and_total() -> None:
    with scoped_app() as (app, world):
        m = _migration(world.corpus_a.id)
        world.session.execute = AsyncMock(
            side_effect=[
                _result(scalars=[world.corpus_a.id]),
                _result(scalar=3),
                _result(scalars=[m]),
            ]
        )
        res = await call(
            app, "tk_a", "GET", "/api/v1/migrations?status=pending_approval&offset=2&limit=1"
        )
        page = world.session.execute.await_args_list[2].args[0]
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["total"], body["offset"], body["limit"]) == (3, 2, 1)
    assert body["items"][0]["id"] == str(m.id)
    sql = str(page.compile(dialect=postgresql.dialect()))
    assert "corpus_migrations.corpus_id IN" in sql and "corpus_migrations.status" in sql


@pytest.mark.parametrize(
    ("key", "status"), [("admin", 200), ("tk_b", 200), ("tk_a", 403), ("ck_b", 403)]
)
async def test_detail_scoping(key, status) -> None:
    with scoped_app() as (app, world):
        m = _migration(world.corpus_b.id)
        world.add_row(CorpusMigration, m)
        res = await call(app, key, "GET", f"/api/v1/migrations/{m.id}")
    assert res.status_code == status, res.text


@pytest.mark.parametrize("action", ["approve", "cancel", "retry", "rollback"])
@pytest.mark.parametrize("key", ["tk_b", "ck_b"])
async def test_actions_stay_admin_only(action, key) -> None:
    with scoped_app() as (app, world):
        m = _migration(world.corpus_b.id)
        world.add_row(CorpusMigration, m)
        res = await call(app, key, "POST", f"/api/v1/migrations/{m.id}/{action}")
    assert res.status_code == 403


@pytest.mark.parametrize(
    ("key", "status"), [("admin", 200), ("tk_a", 200), ("ck_a", 200), (None, 401)]
)
async def test_embedding_catalog_for_any_key(key, status) -> None:
    with scoped_app() as (app, _):
        res = await call(app, key, "GET", "/api/v1/models/embeddings")
    assert res.status_code == status
    if status == 200:
        assert any(e["model"] for e in res.json())
