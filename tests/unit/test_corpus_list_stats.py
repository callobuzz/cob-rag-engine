"""Corpus list/detail carry owner, counts and last activity; filter, page, move (G-7)."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from tests.unit.scope_harness import call, scoped_app


@pytest.fixture(autouse=True)
def _no_cache_invalidation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "callobuzz_rag.api.routes.corpus.invalidate_corpus_query_cache", AsyncMock()
    )


async def test_stats_statement_is_one_query_per_page() -> None:
    from callobuzz_rag.api.routes.corpus import corpus_stats

    session = AsyncMock()
    ids = [uuid.uuid4(), uuid.uuid4()]
    when = datetime(2026, 9, 30, 10, tzinfo=UTC)
    tenant_id = uuid.uuid4()
    result = MagicMock()
    result.all.return_value = [
        (ids[0], tenant_id, "Acme", 3, 41, when),
        (ids[1], tenant_id, "Acme", 0, 0, None),
    ]
    session.execute = AsyncMock(return_value=result)

    stats = await corpus_stats(session, ids)

    assert session.execute.await_count == 1
    sql = str(session.execute.await_args.args[0].compile(dialect=postgresql.dialect()))
    assert "greatest(" in sql.lower()
    assert "max(query_log.created_at)" in sql and "max(ingest_jobs.created_at)" in sql
    assert "document_versions.status" in sql  # chunks of active versions only
    assert "JOIN tenants" in sql and "OUTER JOIN" not in sql  # every corpus has a tenant
    assert stats[ids[0]].model_dump() == {
        "tenant_id": tenant_id,
        "tenant_name": "Acme",
        "document_count": 3,
        "chunk_count": 41,
        "last_activity_at": when,
    }
    assert stats[ids[1]].document_count == 0 and stats[ids[1]].last_activity_at is None


async def test_no_ids_no_query() -> None:
    from callobuzz_rag.api.routes.corpus import corpus_stats

    session = AsyncMock()
    assert await corpus_stats(session, []) == {}
    session.execute.assert_not_awaited()


def _list_session(world, corpora, total):
    """count -> total, page -> corpora, stats -> one row per corpus."""
    count = MagicMock()
    count.scalar.return_value = total
    page = MagicMock()
    page.scalars.return_value.all.return_value = corpora
    stats = MagicMock()
    stats.all.return_value = [(c.id, c.tenant_id, "T", 2, 5, None) for c in corpora]
    world.session.execute = AsyncMock(side_effect=[count, page, stats])


async def test_list_items_carry_stats_and_paging() -> None:
    with scoped_app() as (app, world):
        _list_session(world, [world.corpus_a], total=7)
        res = await call(app, "admin", "GET", "/api/v1/corpora?offset=5&limit=1")
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["count"], body["total"], body["offset"], body["limit"]) == (1, 7, 5, 1)
    item = body["items"][0]
    assert item["tenant_id"] == str(world.tenant_a.id)
    assert (item["tenant_name"], item["document_count"], item["chunk_count"]) == ("T", 2, 5)
    assert "last_activity_at" in item


async def test_admin_filters_by_tenant() -> None:
    with scoped_app() as (app, world):
        _list_session(world, [world.corpus_b], total=1)
        res = await call(app, "admin", "GET", f"/api/v1/corpora?tenant_id={world.tenant_b.id}")
        assert res.status_code == 200
        where = str(world.session.execute.await_args_list[0].args[0])
    assert "corpora.tenant_id" in where


async def test_tenant_cannot_list_another_tenants_corpora() -> None:
    with scoped_app() as (app, world):
        res = await call(app, "tk_a", "GET", f"/api/v1/corpora?tenant_id={world.tenant_b.id}")
    assert res.status_code == 403


@pytest.mark.parametrize("limit", [0, 501])
async def test_limit_bounds(limit) -> None:
    with scoped_app() as (app, _):
        res = await call(app, "admin", "GET", f"/api/v1/corpora?limit={limit}")
    assert res.status_code == 422


async def test_detail_carries_stats() -> None:
    with scoped_app() as (app, world):
        stats = MagicMock()
        stats.all.return_value = [(world.corpus_a.id, world.tenant_a.id, "Tenant A", 4, 9, None)]
        world.session.execute = AsyncMock(return_value=stats)
        res = await call(app, "tk_a", "GET", f"/api/v1/corpora/{world.corpus_a.id}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["tenant_name"] == "Tenant A" and body["document_count"] == 4


def _count(n: int) -> MagicMock:
    result = MagicMock()
    result.scalar.return_value = n
    result.all.return_value = []
    return result


def _kb_limit(limit: int | None):  # type: ignore[no-untyped-def]
    """The target's effective knowledge-base limit (accounts A6: override, else plan)."""
    from unittest.mock import patch

    from callobuzz_rag.accounts import caps

    return patch.object(
        caps, "effective_limits", AsyncMock(return_value=caps.Limits(max_knowledge_bases=limit))
    )


async def test_admin_moves_a_corpus_to_another_tenant() -> None:
    with scoped_app() as (app, world), _kb_limit(10):
        world.session.execute = AsyncMock(return_value=_count(0))
        res = await call(
            app,
            "admin",
            "PUT",
            f"/api/v1/corpora/{world.corpus_a.id}",
            json={"tenant_id": str(world.tenant_b.id)},
        )
    assert res.status_code == 200, res.text
    assert world.corpus_a.tenant_id == world.tenant_b.id


async def test_admin_cannot_detach_a_corpus() -> None:
    """Every corpus belongs to a tenant (accounts A1): `tenant_id: null` is refused."""
    with scoped_app() as (app, world):
        world.session.execute = AsyncMock(return_value=_count(0))
        res = await call(
            app, "admin", "PUT", f"/api/v1/corpora/{world.corpus_a.id}", json={"tenant_id": None}
        )
    assert res.status_code == 422, res.text
    assert res.json()["code"] == "tenant_required"
    assert world.corpus_a.tenant_id == world.tenant_a.id


async def test_move_respects_the_target_limit() -> None:
    with scoped_app() as (app, world), _kb_limit(10):
        world.session.execute = AsyncMock(return_value=_count(10))  # tenant B is full
        res = await call(
            app,
            "admin",
            "PUT",
            f"/api/v1/corpora/{world.corpus_a.id}",
            json={"tenant_id": str(world.tenant_b.id)},
        )
    assert res.status_code == 409
    assert res.json() == {
        "detail": "Tenant corpus limit reached (10)",
        "code": "kb_limit_reached",
        "limit": 10,
    }
    assert world.corpus_a.tenant_id == world.tenant_a.id


async def test_move_to_unknown_tenant_is_404() -> None:
    with scoped_app() as (app, world):
        res = await call(
            app,
            "admin",
            "PUT",
            f"/api/v1/corpora/{world.corpus_a.id}",
            json={"tenant_id": str(uuid.uuid4())},
        )
    assert res.status_code == 404


async def test_tenant_cannot_move_its_corpus() -> None:
    with scoped_app() as (app, world):
        res = await call(
            app,
            "tk_a",
            "PUT",
            f"/api/v1/corpora/{world.corpus_a.id}",
            json={"tenant_id": str(world.tenant_b.id)},
        )
    assert res.status_code == 403
    assert world.corpus_a.tenant_id == world.tenant_a.id


async def test_resending_the_same_tenant_is_not_a_move() -> None:
    """A tenant PUT that echoes its own tenant_id back is not refused."""
    with scoped_app() as (app, world):
        world.session.execute = AsyncMock(return_value=_count(0))
        res = await call(
            app,
            "tk_a",
            "PUT",
            f"/api/v1/corpora/{world.corpus_a.id}",
            json={"tenant_id": str(world.tenant_a.id), "name": "Renamed"},
        )
    assert res.status_code == 200, res.text
    assert world.corpus_a.name == "Renamed"


@pytest.mark.parametrize(
    ("key", "which", "status"),
    [("tk_a", "b", 403), ("ck_a", "a", 403), (None, "a", 401)],
)
async def test_corpus_routes_scoping(key, which, status) -> None:
    with scoped_app() as (app, world):
        res = await call(app, key, "GET", f"/api/v1/corpora/{world.corpus(which).id}")
    assert res.status_code == status
