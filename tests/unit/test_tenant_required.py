"""Every knowledge base belongs to a tenant (accounts A1).

The admin key must name a tenant to create a corpus and can no longer detach one; a
tenant key still creates corpora for itself without naming it.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from tests.unit.scope_harness import call, scoped_app

TENANT_REQUIRED = {
    "detail": "tenant_id is required: every knowledge base belongs to a tenant",
    "code": "tenant_required",
}


@pytest.fixture(autouse=True)
def _no_cache_invalidation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "callobuzz_rag.api.routes.corpus.invalidate_corpus_query_cache", AsyncMock()
    )


def _creating_session(world) -> None:
    def flush_assigns_id() -> None:
        for call_ in world.session.add.call_args_list:
            obj = call_.args[0]
            obj.id = obj.id or uuid.uuid4()
            obj.created_at = datetime(2026, 9, 30, tzinfo=UTC)

    world.session.flush = AsyncMock(side_effect=flush_assigns_id)


async def test_admin_create_without_tenant_is_422_tenant_required() -> None:
    with scoped_app() as (app, world):
        _creating_session(world)
        res = await call(app, "admin", "POST", "/api/v1/corpora", json={"name": "Orphan"})
    assert res.status_code == 422
    assert res.json() == TENANT_REQUIRED
    world.session.add.assert_not_called()
    app.state.vector_store.create_collection.assert_not_called()


async def test_admin_create_with_null_tenant_is_422_tenant_required() -> None:
    with scoped_app() as (app, world):
        _creating_session(world)
        res = await call(
            app, "admin", "POST", "/api/v1/corpora", json={"name": "Orphan", "tenant_id": None}
        )
    assert res.status_code == 422
    assert res.json()["code"] == "tenant_required"


async def test_admin_create_with_unknown_tenant_is_404() -> None:
    with scoped_app() as (app, world):
        _creating_session(world)
        res = await call(
            app,
            "admin",
            "POST",
            "/api/v1/corpora",
            json={"name": "X", "tenant_id": str(uuid.uuid4())},
        )
    assert res.status_code == 404
    world.session.add.assert_not_called()


async def test_admin_create_for_a_tenant_is_201() -> None:
    with scoped_app() as (app, world):
        _creating_session(world)
        res = await call(
            app,
            "admin",
            "POST",
            "/api/v1/corpora",
            json={"name": "Help", "tenant_id": str(world.tenant_b.id)},
        )
        created = world.session.add.call_args_list[0].args[0]  # the corpus, then its key
    assert res.status_code == 201, res.text
    assert created.tenant_id == world.tenant_b.id


async def test_tenant_key_create_without_tenant_id_is_its_own() -> None:
    with scoped_app() as (app, world):
        _creating_session(world)
        res = await call(app, "tk_a", "POST", "/api/v1/corpora", json={"name": "Mine"})
        created = world.session.add.call_args_list[0].args[0]  # the corpus, then its key
    assert res.status_code == 201, res.text
    assert created.tenant_id == world.tenant_a.id


@pytest.mark.parametrize("key", ["admin", "tk_a"])
async def test_put_null_tenant_is_422_and_changes_nothing(key) -> None:
    with scoped_app() as (app, world):
        res = await call(
            app,
            key,
            "PUT",
            f"/api/v1/corpora/{world.corpus_a.id}",
            json={"tenant_id": None, "name": "Renamed"},
        )
    assert res.status_code == 422
    assert res.json() == TENANT_REQUIRED
    assert world.corpus_a.tenant_id == world.tenant_a.id
    assert world.corpus_a.name == "Corpus A"  # refused before any other change
    world.session.commit.assert_not_awaited()


async def test_admin_move_to_another_tenant_still_works() -> None:
    with scoped_app() as (app, world):
        res = await call(
            app,
            "admin",
            "PUT",
            f"/api/v1/corpora/{world.corpus_a.id}",
            json={"tenant_id": str(world.tenant_b.id)},
        )
    assert res.status_code == 200, res.text
    assert world.corpus_a.tenant_id == world.tenant_b.id


def test_models_require_a_tenant() -> None:
    """NOT NULL on both columns; the corpus FK restricts instead of nulling."""
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.tenant import Tenant
    from callobuzz_rag.db.models.usage import UsageDaily

    column = Corpus.__table__.c.tenant_id
    assert column.nullable is False
    (fk,) = column.foreign_keys
    assert fk.ondelete == "RESTRICT"
    assert UsageDaily.__table__.c.tenant_id.nullable is False
    assert Tenant.corpora.property.passive_deletes == "all"
