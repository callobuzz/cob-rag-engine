"""The website-learning routes and the widget-key triggers, on the in-memory database."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.document import Document
from callobuzz_rag.db.models.document_version import DocumentVersion
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.website import (
    WebsitePage,
    WebsiteRun,
    WebsiteSettings,
    WebsiteSource,
)
from callobuzz_rag.db.models.widget_key import WidgetKey
from callobuzz_rag.website import netguard, service
from tests.unit.test_auth_routes import ADMIN, PASSWORD, World, auth_app, login, req

pytestmark = pytest.mark.usefixtures("unlimited_plan")

NOW = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
SITE = "https://shreedental.in"


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    async def public(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(netguard, "resolve_host", public)


@pytest.fixture
def dispatched():
    with (
        patch.object(service, "dispatch_learn", MagicMock(return_value=True)) as learn,
        patch.object(service, "dispatch_urls", MagicMock(return_value=True)) as urls,
    ):
        yield MagicMock(learn=learn, urls=urls)


def make_corpus(world: World, name: str = "Shree Dental", tenant: Any = None) -> Corpus:
    return world.db.add(
        Corpus(
            name=name,
            tenant_id=(tenant or world.shree).id,
            qdrant_collection=f"corpus_{uuid.uuid4().hex[:8]}",
            api_key_hash="",
        )
    )


def add_key(world: World, corpus: Corpus, origins: list[str], *, active: bool = True) -> WidgetKey:
    return world.db.add(
        WidgetKey(
            corpus_id=corpus.id,
            name="Site",
            public_key=f"pk_{uuid.uuid4().hex[:10]}",
            allowed_origins=origins,
            is_active=active,
        )
    )


def add_source(world: World, corpus: Corpus, scope: str = "shreedental.in", **kw: Any) -> Any:
    return world.db.add(
        WebsiteSource(
            corpus_id=corpus.id,
            tenant_id=corpus.tenant_id,
            origin=f"https://{scope}",
            scope=scope,
            **kw,
        )
    )


def add_page(world: World, source: Any, path: str, **kw: Any) -> Any:
    fields = {"status": "learned", **kw}
    return world.db.add(
        WebsitePage(
            source_id=source.id,
            corpus_id=source.corpus_id,
            url=f"{SITE}{path}",
            url_hash=uuid.uuid4().hex,
            **fields,
        )
    )


def add_document(world: World, corpus: Corpus, page: Any) -> Any:
    doc = world.db.add(Document(corpus_id=corpus.id, external_id=f"web:{page.url}"))
    world.db.add(DocumentVersion(document_id=doc.id, status="active"))
    page.document_id = doc.id
    return doc


async def api(app: Any, method: str, corpus: Corpus, path: str = "", **kw: Any) -> Any:
    return await req(app, method, f"/api/v1/corpora/{corpus.id}/website{path}", key=ADMIN, **kw)


async def _refresh_with_server_defaults(self: Any, obj: Any) -> None:
    """What PostgreSQL fills in on a row a route just created (the in-memory database does
    not know server defaults)."""
    for column in obj.__table__.columns:
        if getattr(obj, column.key, None) is not None or column.server_default is None:
            continue
        text = str(getattr(column.server_default, "arg", "")).strip()
        values = {"true": True, "false": False, "'[]'::jsonb": [], "'{}'::jsonb": {}}
        if text in values:
            setattr(obj, column.key, values[text])
        elif text.isdigit():
            setattr(obj, column.key, int(text))


def with_app(world: World):  # type: ignore[no-untyped-def]
    from contextlib import contextmanager

    from tests.unit.accounts_fakes import FakeSession

    @contextmanager
    def cm():  # type: ignore[no-untyped-def]
        with (
            auth_app(world) as app,
            patch.object(FakeSession, "refresh", _refresh_with_server_defaults),
        ):
            app.state.vector_store = AsyncMock()
            yield app

    return cm()


def audits(world: World, action: str) -> list[AuditLog]:
    return world.audits(action)


# ------------------------------------------------------------------ overview


async def test_a_fresh_knowledge_base_lists_its_allowed_sites_but_no_sources() -> None:
    world = World()
    corpus = make_corpus(world)
    add_key(
        world,
        corpus,
        [
            SITE,
            "https://www.shreedental.in",
            "*",
            "http://localhost:3000",
            "https://x.googleusercontent.com",
        ],
    )
    add_key(world, corpus, ["https://old.example.in"], active=False)
    other = make_corpus(world, "Other")
    add_key(world, other, ["https://other.example.in"])
    with with_app(world) as app:
        res = await api(app, "GET", corpus)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["sources"] == []
    assert body["unlearned_origins"] == [SITE]  # apex and www are one site; * and localhost never
    assert body["settings"] == {
        "enabled": True,
        "exclude_patterns": [],
        "last_reread_at": None,
        "refresh_mode": "daily",  # BIZ2 freshness
        "adaptive": True,
    }
    assert body["plan"] == {
        "learning_enabled": True,
        "pages_cap": 2000,
        "pages_learned": 0,
        "reason": None,
    }


async def test_the_overview_shows_sources_and_the_plans_cap() -> None:
    world = World()
    world.db.default_plan.website_pages_max = 50
    corpus = make_corpus(world)
    add_key(world, corpus, [SITE])
    add_source(world, corpus, pages_found=30, pages_learned=12, status="ready")
    with with_app(world) as app:
        body = (await api(app, "GET", corpus)).json()
    assert body["unlearned_origins"] == []
    assert body["sources"][0]["scope"] == "shreedental.in"
    assert body["sources"][0]["pages_learned"] == 12
    assert body["plan"] == {
        "learning_enabled": True,
        "pages_cap": 50,
        "pages_learned": 12,
        "reason": None,
    }


# ------------------------------------------------------------------ learn


async def test_learn_refuses_what_is_not_the_owners_to_read(dispatched: Any) -> None:
    world = World()
    corpus = make_corpus(world)
    add_key(world, corpus, [SITE])
    with with_app(world) as app:
        bad = await api(app, "POST", corpus, "/learn", json={"origin": "not a url"})
        other = await api(app, "POST", corpus, "/learn", json={"origin": "https://example.com"})
    assert (bad.status_code, bad.json()["code"]) == (422, "website_url_invalid")
    assert bad.json()["detail"] == "Enter your website like shreedental.in"
    assert (other.status_code, other.json()["code"]) == (422, "website_not_allowed")
    dispatched.learn.assert_not_called()


async def test_learn_refuses_a_private_address(
    dispatched: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def private(host: str, port: int) -> list[str]:
        return ["10.0.0.5"]

    monkeypatch.setattr(netguard, "resolve_host", private)
    world = World()
    corpus = make_corpus(world)
    add_key(world, corpus, [SITE])
    with with_app(world) as app:
        res = await api(app, "POST", corpus, "/learn", json={"origin": SITE})
    assert (res.status_code, res.json()["code"]) == (422, "website_blocked")
    dispatched.learn.assert_not_called()


async def test_the_overview_says_why_learning_is_off(dispatched: Any) -> None:
    world = World()
    world.db.default_plan.website_learning_enabled = False
    corpus = make_corpus(world)
    with with_app(world) as app:
        res = await api(app, "GET", corpus, "")
    assert res.status_code == 200, res.text
    assert res.json()["plan"]["learning_enabled"] is False
    assert res.json()["plan"]["reason"] == "plan"


async def test_learn_is_refused_when_the_plan_turns_it_off(dispatched: Any) -> None:
    world = World()
    world.db.default_plan.website_learning_enabled = False
    corpus = make_corpus(world)
    add_key(world, corpus, [SITE])
    with with_app(world) as app:
        res = await api(app, "POST", corpus, "/learn", json={"origin": SITE})
    assert (res.status_code, res.json()["code"]) == (409, "website_learning_disabled")
    assert res.json()["reason"] == "plan"
    dispatched.learn.assert_not_called()


async def test_learn_starts_a_first_read_and_a_repeat_while_it_runs_starts_nothing(
    dispatched: Any,
) -> None:
    world = World()
    corpus = make_corpus(world)
    add_key(world, corpus, [SITE, "https://www.shreedental.in"])
    with with_app(world) as app:
        first = await api(app, "POST", corpus, "/learn", json={"origin": "shreedental.in"})
        (source,) = world.db.all(WebsiteSource)
        world.db.add(
            WebsiteRun(
                source_id=source.id,
                corpus_id=corpus.id,
                kind="first",
                trigger="manual",
                started_at=NOW,
                heartbeat_at=datetime.now(UTC),
                counts={},
            )
        )
        again = await api(app, "POST", corpus, "/learn", json={"origin": SITE})
    assert first.status_code == 202, first.text
    assert first.json()["scope"] == "shreedental.in" and first.json()["status"] == "discovering"
    assert again.status_code == 202 and again.json()["id"] == first.json()["id"]
    dispatched.learn.assert_called_once_with(source.id, "first", "manual")
    assert len(world.db.all(WebsiteSource)) == 1
    (row,) = audits(world, "website.learning_started")
    assert row.details["trigger"] == "manual" and row.details["origin"] == SITE


async def test_a_queue_that_is_down_is_a_503_and_leaves_the_site_idle() -> None:
    world = World()
    corpus = make_corpus(world)
    add_key(world, corpus, [SITE])
    with (
        with_app(world) as app,
        patch.object(service, "dispatch_learn", MagicMock(return_value=False)),
    ):
        res = await api(app, "POST", corpus, "/learn", json={"origin": SITE})
    assert res.status_code == 503
    assert world.db.all(WebsiteSource)[0].status == "idle"


# ------------------------------------------------------------------ widget key triggers


def _key_body(*origins: str) -> dict[str, Any]:
    return {"name": "Website", "allowed_origins": list(origins)}


async def _make_key(app: Any, corpus: Corpus, *origins: str) -> Any:
    return await req(
        app,
        "POST",
        f"/api/v1/corpora/{corpus.id}/widget-keys",
        key=ADMIN,
        json=_key_body(*origins),
    )


async def test_a_new_widget_key_starts_one_read_for_both_spellings(dispatched: Any) -> None:
    world = World()
    corpus = make_corpus(world)
    with with_app(world) as app:
        res = await _make_key(app, corpus, SITE, "https://www.shreedental.in")
    assert res.status_code == 201, res.text
    (source,) = world.db.all(WebsiteSource)
    assert source.scope == "shreedental.in" and source.origin == SITE
    dispatched.learn.assert_called_once_with(source.id, "first", "origin_added")
    (row,) = audits(world, "website.learning_started")
    assert row.details["trigger"] == "origin_added"


async def test_a_wildcard_and_localhost_never_start_a_read(dispatched: Any) -> None:
    world = World()
    corpus = make_corpus(world)
    with with_app(world) as app:
        res = await _make_key(app, corpus, "*", "http://localhost:3000")
    assert res.status_code == 201, res.text
    assert world.db.all(WebsiteSource) == []
    dispatched.learn.assert_not_called()


async def test_a_key_of_another_knowledge_base_does_not_make_this_one_learn(
    dispatched: Any,
) -> None:
    world = World()
    mine, other = make_corpus(world), make_corpus(world, "Other")
    with with_app(world) as app:
        await _make_key(app, other, SITE)
    assert world.db.all(WebsiteSource)[0].corpus_id == other.id
    assert [s for s in world.db.all(WebsiteSource) if s.corpus_id == mine.id] == []


async def test_changing_a_key_adds_removes_and_resumes_sites(dispatched: Any) -> None:
    world = World()
    corpus = make_corpus(world)
    with with_app(world) as app:
        created = (await _make_key(app, corpus, SITE)).json()
        (source,) = world.db.all(WebsiteSource)
        dispatched.learn.reset_mock()
        path = f"/api/v1/corpora/{corpus.id}/widget-keys/{created['id']}"

        same = await req(  # a subdomain of the same site: nothing new
            app,
            "PATCH",
            path,
            key=ADMIN,
            json={"allowed_origins": [SITE, "https://blog.shreedental.in"]},
        )
        assert same.status_code == 200, same.text
        assert len(world.db.all(WebsiteSource)) == 1
        dispatched.learn.assert_not_called()

        gone = await req(
            app, "PATCH", path, key=ADMIN, json={"allowed_origins": ["https://other.example.in"]}
        )
        assert gone.status_code == 200, gone.text
        scopes = {s.scope: s for s in world.db.all(WebsiteSource)}
        assert (scopes["shreedental.in"].status, scopes["shreedental.in"].pause_reason) == (
            "paused",
            "origin_removed",
        )
        assert scopes["other.example.in"].status == "discovering"

        back = await req(app, "PATCH", path, key=ADMIN, json={"allowed_origins": [SITE]})
        assert back.status_code == 200
    assert source.status == "discovering" and source.pause_reason is None
    triggers = [c.args[2] for c in dispatched.learn.call_args_list]
    assert triggers == ["origin_added", "origin_added"]


async def test_deleting_the_key_pauses_the_site(dispatched: Any) -> None:
    world = World()
    corpus = make_corpus(world)
    with with_app(world) as app:
        created = (await _make_key(app, corpus, SITE)).json()
        res = await req(
            app, "DELETE", f"/api/v1/corpora/{corpus.id}/widget-keys/{created['id']}", key=ADMIN
        )
    assert res.status_code == 204
    (source,) = world.db.all(WebsiteSource)
    assert (source.status, source.pause_reason) == ("paused", "origin_removed")


async def test_a_learning_failure_never_fails_the_key_request() -> None:
    world = World()
    corpus = make_corpus(world)
    with (
        with_app(world) as app,
        patch.object(service, "on_origins_changed", AsyncMock(side_effect=RuntimeError("boom"))),
    ):
        res = await _make_key(app, corpus, SITE)
    assert res.status_code == 201


async def test_a_plan_that_turns_learning_off_makes_no_source_from_a_key(dispatched: Any) -> None:
    world = World()
    world.db.default_plan.website_learning_enabled = False
    corpus = make_corpus(world)
    with with_app(world) as app:
        res = await _make_key(app, corpus, SITE)
    assert res.status_code == 201
    assert world.db.all(WebsiteSource) == []


# ------------------------------------------------------------------ settings


async def test_exclusion_patterns_archive_matching_pages_and_are_audited(dispatched: Any) -> None:
    world = World()
    corpus = make_corpus(world)
    source = add_source(world, corpus)
    blog = add_page(world, source, "/blog/post")
    add_document(world, corpus, blog)
    keep = add_page(world, source, "/pricing")
    with with_app(world) as app:
        res = await api(app, "PATCH", corpus, json={"exclude_patterns": ["/blog/*", "/blog/*"]})
        store = app.state.vector_store
    assert res.status_code == 200, res.text
    assert res.json()["settings"]["exclude_patterns"] == ["/blog/*"]
    assert (blog.status, blog.skip_reason) == ("excluded", "pattern")
    assert keep.status == "learned"
    version = next(v for v in world.db.all(DocumentVersion) if v.document_id == blog.document_id)
    assert version.status == "inactive"
    store.delete_by_filter.assert_awaited()
    (row,) = audits(world, "website.settings_updated")
    assert row.details == {"corpus_id": str(corpus.id), "changed": ["exclude_patterns"]}


async def test_removing_a_pattern_queues_the_pages_again(dispatched: Any) -> None:
    world = World()
    corpus = make_corpus(world)
    source = add_source(world, corpus)
    page = add_page(world, source, "/blog/post", status="excluded", skip_reason="pattern")
    world.db.add(
        WebsiteSettings(
            corpus_id=corpus.id, tenant_id=corpus.tenant_id, exclude_patterns=["/blog/*"]
        )
    )
    with with_app(world) as app:
        res = await api(app, "PATCH", corpus, json={"exclude_patterns": []})
    assert res.status_code == 200
    assert page.status == "discovered"
    dispatched.urls.assert_called_once_with(source.id, [page.url])


async def test_switching_learning_off_pauses_the_sites(dispatched: Any) -> None:
    world = World()
    corpus = make_corpus(world)
    source = add_source(world, corpus, status="ready")
    with with_app(world) as app:
        res = await api(app, "PATCH", corpus, json={"enabled": False})
        assert res.json()["settings"]["enabled"] is False
        assert (source.status, source.pause_reason) == ("paused", "disabled")
        await api(app, "PATCH", corpus, json={"enabled": True})
    assert (source.status, source.pause_reason) == ("idle", None)


@pytest.mark.parametrize(
    "patterns", [[f"/p{i}" for i in range(51)], ["/" + "a" * 200], [""], [" "]]
)
async def test_bad_patterns_are_refused(patterns: list[str]) -> None:
    world = World()
    corpus = make_corpus(world)
    with with_app(world) as app:
        res = await api(app, "PATCH", corpus, json={"exclude_patterns": patterns})
    assert res.status_code == 422


async def test_a_member_cannot_change_what_is_learned(dispatched: Any) -> None:
    world = World()
    corpus = make_corpus(world)
    source = add_source(world, corpus)
    page = add_page(world, source, "/pricing")
    member = world.user("ravi@shree.example")
    world.member(member, world.shree, "member")
    with with_app(world) as app:
        token = (await login(app, "ravi@shree.example", PASSWORD)).json()["access_token"]
        base = f"/api/v1/corpora/{corpus.id}/website"
        calls = [
            ("PATCH", base, {"enabled": False}),
            ("DELETE", f"{base}/sources/{source.id}", None),
            ("PATCH", f"{base}/pages/{page.id}", {"excluded": True}),
        ]
        for method, path, body in calls:
            res = await req(app, method, path, token=token, json=body)
            assert res.status_code == 403 and res.json()["code"] == "owner_required", path
        seen = await req(app, "GET", base, token=token)
        reread = await req(app, "POST", f"{base}/pages/{page.id}/reread", token=token)
    assert seen.status_code == 200
    assert reread.status_code == 202
    assert world.db.all(WebsiteSource) == [source] and page.status == "learned"


# ------------------------------------------------------------------ pages


async def test_excluding_a_page_archives_it_and_including_queues_it(dispatched: Any) -> None:
    world = World()
    corpus = make_corpus(world)
    source = add_source(world, corpus)
    page = add_page(world, source, "/pricing")
    add_document(world, corpus, page)
    with with_app(world) as app:
        out = await api(app, "PATCH", corpus, f"/pages/{page.id}", json={"excluded": True})
        store = app.state.vector_store
        assert out.status_code == 200, out.text
        assert out.json()["status"] == "excluded" and out.json()["excluded_by_owner"] is True
        store.delete_by_filter.assert_awaited()
        dispatched.urls.assert_not_called()
        back = await api(app, "PATCH", corpus, f"/pages/{page.id}", json={"excluded": False})
    assert back.json()["excluded_by_owner"] is False and back.json()["status"] == "queued"
    dispatched.urls.assert_called_once_with(source.id, [page.url])
    assert len(audits(world, "website.page_excluded")) == 1
    assert len(audits(world, "website.page_included")) == 1


async def test_another_knowledge_bases_page_is_not_found(dispatched: Any) -> None:
    world = World()
    mine, other = make_corpus(world), make_corpus(world, "Other")
    theirs = add_page(world, add_source(world, other), "/pricing")
    with with_app(world) as app:
        patch_res = await api(app, "PATCH", mine, f"/pages/{theirs.id}", json={"excluded": True})
        reread = await api(app, "POST", mine, f"/pages/{theirs.id}/reread")
        missing = await api(app, "DELETE", mine, f"/sources/{uuid.uuid4()}")
    for res in (patch_res, reread):
        assert (res.status_code, res.json()["code"]) == (404, "website_page_not_found")
    assert (missing.status_code, missing.json()["code"]) == (404, "website_source_not_found")
    assert theirs.status == "learned"


async def test_a_page_reread_queues_that_url(dispatched: Any) -> None:
    world = World()
    corpus = make_corpus(world)
    source = add_source(world, corpus)
    page = add_page(world, source, "/pricing")
    with with_app(world) as app:
        res = await api(app, "POST", corpus, f"/pages/{page.id}/reread")
    assert res.status_code == 202 and res.json()["url"] == page.url
    dispatched.urls.assert_called_once_with(source.id, [page.url])
    (row,) = audits(world, "website.reread_requested")
    assert row.details["scope"] == "page"


async def test_the_page_list_shapes_rows_counts_and_the_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World()
    corpus = make_corpus(world)
    source = add_source(world, corpus)
    skipped = add_page(world, source, "/cart", status="skipped", skip_reason="private_page")
    seen: dict[str, Any] = {}

    async def fake_list(session: Any, corpus_id: Any, **kw: Any) -> Any:
        seen.update(kw)
        return [skipped], 1, {"skipped": 1, "learned": 4}, {"private_page": 1}

    monkeypatch.setattr(service, "list_pages", fake_list)
    with with_app(world) as app:
        res = await api(
            app,
            "GET",
            corpus,
            f"/pages?status=skipped&q=cart&source_id={source.id}&limit=20&offset=5",
        )
        too_many = await api(app, "GET", corpus, "/pages?limit=101")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 1 and body["counts"] == {"skipped": 1, "learned": 4}
    assert body["reasons"] == {"private_page": 1}
    assert (
        body["items"][0]["skip_reason"] == "private_page" and body["items"][0]["url"] == skipped.url
    )
    assert seen == {
        "status": "skipped",
        "source_id": source.id,
        "q": "cart",
        "offset": 5,
        "limit": 20,
    }
    assert too_many.status_code == 422


async def test_the_waiting_chip_filters_found_and_queued_pages() -> None:
    statements: list[Any] = []

    class Result:
        def scalar(self) -> int:
            return 0

        def scalars(self) -> "Result":
            return self

        def all(self) -> list[Any]:
            return []

    class Recorder:
        async def execute(self, stmt: Any) -> Result:
            statements.append(stmt)
            return Result()

    await service.list_pages(
        Recorder(),  # type: ignore[arg-type]
        uuid.uuid4(),
        status="waiting",
        source_id=None,
        q=None,
        offset=0,
        limit=25,
    )
    sql = str(statements[1].compile(dialect=postgresql.dialect()))
    assert "website_pages.status IN" in sql


async def test_the_page_queries_are_valid_postgresql() -> None:
    statements: list[Any] = []

    class Result:
        def scalar(self) -> int:
            return 0

        def scalars(self) -> "Result":
            return self

        def all(self) -> list[Any]:
            return []

    class Recorder:
        async def execute(self, stmt: Any) -> Result:
            statements.append(stmt)
            return Result()

    await service.list_pages(
        Recorder(),  # type: ignore[arg-type]
        uuid.uuid4(),
        status="skipped",
        source_id=uuid.uuid4(),
        q="100%_off",
        offset=10,
        limit=25,
    )
    sql = [str(s.compile(dialect=postgresql.dialect())) for s in statements]
    assert len(sql) == 4
    assert "count(*)" in sql[0].lower() and "website_pages.status =" in sql[0]
    assert "ILIKE" in sql[1] and "ORDER BY website_pages.rank_score DESC" in sql[1]
    assert "LIMIT" in sql[1] and "OFFSET" in sql[1]
    assert "GROUP BY website_pages.status" in sql[2]
    assert "GROUP BY website_pages.skip_reason" in sql[3]
    assert "website_pages.status IN" in sql[3]  # skipped and owner-excluded pages
    compiled = statements[1].compile(dialect=postgresql.dialect())
    assert "%100off%" in compiled.params.values()  # % and _ in the search are not wildcards


# ------------------------------------------------------------------ reread, remove


async def test_reread_all_is_limited_to_once_every_ten_minutes(dispatched: Any) -> None:
    world = World()
    corpus = make_corpus(world)
    source = add_source(world, corpus, status="ready")
    add_source(world, corpus, "shreedental.co.in", status="paused", pause_reason="origin_removed")
    with with_app(world) as app:
        first = await api(app, "POST", corpus, "/reread")
        second = await api(app, "POST", corpus, "/reread")
    assert first.status_code == 202 and first.json() == {"queued": 1}
    dispatched.learn.assert_called_once_with(source.id, "full", "manual")
    assert second.status_code == 429
    assert second.json()["code"] == "website_reread_too_soon"
    assert 0 < int(second.headers["retry-after"]) <= 600
    assert second.json()["retry_after"] == int(second.headers["retry-after"])
    assert len(audits(world, "website.reread_requested")) == 1


async def test_removing_a_site_archives_its_pages(dispatched: Any) -> None:
    world = World()
    corpus = make_corpus(world)
    source = add_source(world, corpus)
    page = add_page(world, source, "/pricing")
    add_document(world, corpus, page)
    with with_app(world) as app:
        res = await api(app, "DELETE", corpus, f"/sources/{source.id}")
        store = app.state.vector_store
    assert res.status_code == 204
    assert world.db.all(WebsiteSource) == []
    store.delete_by_filter.assert_awaited()
    assert next(iter(world.db.all(DocumentVersion))).status == "inactive"
    (row,) = audits(world, "website.source_removed")
    assert row.details["pages_archived"] == 1 and row.details["origin"] == "https://shreedental.in"


async def test_plan_defaults_exist() -> None:
    world = World()
    assert isinstance(world.db.default_plan, Plan)
    assert world.db.default_plan.website_learning_enabled in (True, None)


# ------------------------------------------------------------------ page reads are guarded


async def test_a_page_reread_is_refused_when_learning_is_off_or_the_site_is_paused(
    dispatched: Any,
) -> None:
    world = World()
    corpus = make_corpus(world)
    source = add_source(world, corpus)
    page = add_page(world, source, "/pricing")
    with with_app(world) as app:
        source.status, source.pause_reason = "paused", "origin_removed"
        paused = await api(app, "POST", corpus, f"/pages/{page.id}/reread")
        source.status, source.pause_reason = "ready", None
        world.db.default_plan.website_learning_enabled = False
        off = await api(app, "POST", corpus, f"/pages/{page.id}/reread")
        page.excluded_by_owner, page.status = True, "excluded"
        include = await api(app, "PATCH", corpus, f"/pages/{page.id}", json={"excluded": False})
    assert (paused.status_code, paused.json()["code"]) == (409, "website_source_paused")
    assert (off.status_code, off.json()["code"]) == (409, "website_learning_disabled")
    assert (include.status_code, include.json()["code"]) == (409, "website_learning_disabled")
    assert page.status == "excluded"
    dispatched.urls.assert_not_called()


async def test_a_page_cannot_be_read_again_within_a_minute(dispatched: Any) -> None:
    world = World()
    corpus = make_corpus(world)
    source = add_source(world, corpus)
    page = add_page(world, source, "/pricing", last_checked_at=datetime.now(UTC))
    with with_app(world) as app:
        res = await api(app, "POST", corpus, f"/pages/{page.id}/reread")
        page.status, page.excluded_by_owner = "excluded", True
        include = await api(app, "PATCH", corpus, f"/pages/{page.id}", json={"excluded": False})
        page.last_checked_at = datetime.now(UTC) - timedelta(minutes=2)
        later = await api(app, "POST", corpus, f"/pages/{page.id}/reread")
    for refused in (res, include):
        assert (refused.status_code, refused.json()["code"]) == (429, "website_reread_too_soon")
        assert 1 <= int(refused.headers["retry-after"]) <= 60
    assert later.status_code == 202
    dispatched.urls.assert_called_once_with(source.id, [page.url])


# BIZ2 freshness
async def test_a_new_schedule_reschedules_every_learned_page_at_once(dispatched: Any) -> None:
    from callobuzz_rag.website.freshness import jitter

    world = World()
    corpus = make_corpus(world)
    source = add_source(world, corpus)
    learned = [add_page(world, source, f"/p{i}", interval_seconds=129_600) for i in range(3)]
    skipped = add_page(world, source, "/old", status="skipped")
    with with_app(world) as app:
        res = await api(app, "PATCH", corpus, json={"refresh_mode": "weekly", "adaptive": False})
        row = world.db.all(WebsiteSettings)[0]
    assert res.status_code == 200, res.text
    assert res.json()["settings"]["refresh_mode"] == "weekly"
    assert res.json()["settings"]["adaptive"] is False
    assert (row.refresh_mode, row.adaptive) == ("weekly", False)
    for page in learned:
        assert page.interval_seconds == 604_800
        assert page.next_check_at is not None
        wait = (page.next_check_at - datetime.now(UTC)).total_seconds()
        assert abs(wait - jitter(604_800, page.url_hash)) < 60
    assert skipped.next_check_at is None and skipped.interval_seconds is None
    (audit,) = audits(world, "website.settings_updated")
    assert sorted(audit.details["changed"]) == ["adaptive", "refresh_mode"]


async def test_never_stops_the_checks(dispatched: Any) -> None:
    world = World()
    corpus = make_corpus(world)
    source = add_source(world, corpus)
    page = add_page(
        world, source, "/pricing", interval_seconds=86_400, next_check_at=NOW + timedelta(days=1)
    )
    with with_app(world) as app:
        res = await api(app, "PATCH", corpus, json={"refresh_mode": "off"})
    assert res.status_code == 200, res.text
    assert page.next_check_at is None and page.interval_seconds is None


async def test_an_unchanged_schedule_is_not_audited(dispatched: Any) -> None:
    world = World()
    corpus = make_corpus(world)
    with with_app(world) as app:
        res = await api(app, "PATCH", corpus, json={"refresh_mode": "daily", "adaptive": True})
    assert res.status_code == 200 and audits(world, "website.settings_updated") == []


async def test_an_unknown_schedule_is_refused(dispatched: Any) -> None:
    world = World()
    corpus = make_corpus(world)
    with with_app(world) as app:
        res = await api(app, "PATCH", corpus, json={"refresh_mode": "every-minute"})
        extra = await api(app, "PATCH", corpus, json={"interval": 5})
    assert res.status_code == 422 and extra.status_code == 422


# /BIZ2 freshness
