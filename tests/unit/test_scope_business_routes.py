"""Scoping of the business-mode routes (BIZ3): Home and the articles of a knowledge base.

Every principal of the shared world (`scope_harness.py`) calls them for its own workspace and for
the other one: a workspace's people and its `tk_` key reach their own knowledge base, never
another workspace's; a knowledge-base key reaches only its own; the operator has no Home.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from tests.unit.scope_harness import MATRIX, call, check, expected, scoped_app

BODY = {"kind": "faq", "title": "Do you open on Sunday?", "body": "Yes, 10 am to 2 pm."}
SOME_ID = uuid.uuid4()


def _job() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4())


@pytest.mark.parametrize(("key", "which", "outcome"), MATRIX)
async def test_articles_follow_the_corpus_matrix(key: str | None, which: str, outcome: str) -> None:
    with (
        scoped_app() as (app, world),
        patch("callobuzz_rag.api.routes.articles.queue_records", AsyncMock(return_value=_job())),
    ):
        corpus = world.corpus(which).id
        for method, path, kwargs in [
            ("POST", f"/api/v1/corpora/{corpus}/articles", {"json": BODY}),
            ("GET", f"/api/v1/corpora/{corpus}/articles/{SOME_ID}", {}),
            ("PUT", f"/api/v1/corpora/{corpus}/articles/{SOME_ID}", {"json": BODY}),
        ]:
            res = await call(app, key, method, path, **kwargs)
            body = {"json": BODY} if kwargs else None
            check(expected(key, outcome, method, path, body), res.status_code, res.text)


@pytest.mark.parametrize(("key", "which", "outcome"), MATRIX)
async def test_home_follows_the_corpus_matrix_for_a_named_knowledge_base(
    key: str | None, which: str, outcome: str
) -> None:
    from callobuzz_rag.api.schemas.home import HomeOut

    async def fake_home(
        _session: object, _settings: object, corpus: object, **_kw: object
    ) -> HomeOut:
        from callobuzz_rag.business.home import build_home

        return await build_home(AsyncMock(), _settings, None)  # type: ignore[arg-type]

    with (
        scoped_app() as (app, world),
        patch("callobuzz_rag.api.routes.home.build_home", fake_home),
    ):
        corpus = world.corpus(which).id
        res = await call(app, key, "GET", f"/api/v1/home?corpus_id={corpus}")
    if key in ("admin", "sa") and outcome == "allowed":
        # the operator has no workspace, whichever knowledge base it names
        assert res.status_code == 403 and res.json()["code"] == "no_workspace", res.text
    elif key in ("ck_a", "ck_b"):
        assert res.status_code == 403 and res.json()["code"] == "scope_forbidden", res.text
    else:
        check(outcome, res.status_code, res.text)


async def test_the_home_of_the_workspace_never_names_another_workspaces_knowledge_base() -> None:
    seen: list[object] = []

    async def fake_build(
        _session: object, _settings: object, corpus: object, **_kw: object
    ) -> object:
        seen.append(corpus)

        from callobuzz_rag.business.home import build_home

        return await build_home(AsyncMock(), _settings, None)  # type: ignore[arg-type]

    async def mine(_session: object, tenant_id: uuid.UUID) -> object:
        seen.append(("main", tenant_id))
        return None

    with (
        scoped_app() as (app, world),
        patch("callobuzz_rag.api.routes.home.build_home", fake_build),
        patch("callobuzz_rag.api.routes.home.main_corpus", mine),
    ):
        res = await call(app, "owner_a", "GET", "/api/v1/home")
    assert res.status_code == 200, res.text
    assert ("main", world.tenant_a.id) in seen  # the oldest knowledge base of its own workspace
    assert all(x != ("main", world.tenant_b.id) for x in seen)


@pytest.mark.parametrize("key", ["admin", "sa", "ck_a", None])
async def test_home_refuses_everyone_without_a_workspace(key: str | None) -> None:
    with scoped_app() as (app, _):
        res = await call(app, key, "GET", "/api/v1/home")
    assert res.status_code in (401, 403), res.text
    if key == "ck_a":
        assert res.json()["code"] == "scope_forbidden"
    elif key in ("admin", "sa"):
        assert res.json()["code"] == "no_workspace"


async def test_members_may_write_articles_but_not_delete_the_document() -> None:
    with (
        scoped_app() as (app, world),
        patch("callobuzz_rag.api.routes.articles.queue_records", AsyncMock(return_value=_job())),
    ):
        corpus = world.corpus_a.id
        write = await call(app, "member_a", "POST", f"/api/v1/corpora/{corpus}/articles", json=BODY)
        assert write.status_code not in (401, 403), write.text
        delete = await call(
            app, "member_a", "DELETE", f"/api/v1/documents/{SOME_ID}?corpus_id={corpus}"
        )
        assert delete.status_code == 403 and delete.json()["code"] == "owner_required", delete.text
