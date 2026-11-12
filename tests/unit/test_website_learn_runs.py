"""Website learning runs against a fake database and a fake site: no network, no vector store."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.document import Document
from callobuzz_rag.db.models.document_version import DocumentVersion
from callobuzz_rag.db.models.ingest_job import IngestJob
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.tenant_member import TenantMember
from callobuzz_rag.db.models.user import User
from callobuzz_rag.db.models.website import WebsitePage, WebsiteRun, WebsiteSettings, WebsiteSource
from callobuzz_rag.website import archive as archive_mod
from callobuzz_rag.website import learn as learn_mod
from callobuzz_rag.website.fetch import WebsiteFetcher
from callobuzz_rag.website.learn import LearnDeps, learn_source, learn_urls
from tests.unit.accounts_fakes import FakeDB
from tests.unit.website_fixtures import SiteTransport, html_page, site_transport, words

ORIGIN = "https://shreedental.in"
NOW = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)


def long_page(title: str, seed: str, n: int = 80, **kw: Any) -> str:
    return html_page(title, words(n, seed), **kw)


class World:
    def __init__(
        self, pages: dict[str, Any], *, plan: dict[str, Any] | None = None, **cfg: Any
    ) -> None:
        self.db = FakeDB()
        self.plan = self.db.add(Plan(name="Pune Pro", is_active=True, **(plan or {})))
        self.tenant = self.db.add(Tenant(name="Shree Dental Clinic", plan_id=self.plan.id))
        self.corpus = self.db.add(
            Corpus(
                name="Shree Dental",
                tenant_id=self.tenant.id,
                qdrant_collection="corpus_aaaa0001",
                api_key_hash="",
                is_system=False,
            )
        )
        self.source = self.db.add(
            WebsiteSource(
                corpus_id=self.corpus.id,
                tenant_id=self.tenant.id,
                origin=ORIGIN,
                scope="shreedental.in",
            )
        )
        self.transport: SiteTransport = site_transport({"/robots.txt": "", **pages})
        self.settings = Settings(  # type: ignore[call-arg]
            _env_file=None, website_request_delay_ms=0, website_concurrency=2, **cfg
        )
        self.batches: list[list[Any]] = []
        self.snapshots: list[tuple[int, int]] = []
        self.room: int | None = None
        self.fail_ingest = False
        self.on_ingest: Any = None
        self.vector_store = _Vectors()
        self.clock = NOW

    async def ingest(
        self, batch: list[Any], corpus_id: uuid.UUID, job_id: uuid.UUID
    ) -> dict[str, Any]:
        self.batches.append(batch)
        source = next(iter(self.db.all(WebsiteSource)), None)
        self.snapshots.append((source.pages_found, source.pages_learned) if source else (0, 0))
        if self.on_ingest:
            self.on_ingest()
        if self.fail_ingest:
            return {"status": "failed", "error": "boom", "failed_at_step": "embedding"}
        known = {d.external_id: d for d in self.db.all(Document)}
        for record in batch:  # like the records pipeline: a matching fingerprint is "unchanged"
            fingerprint = record.fingerprint()
            doc = known.get(record.id)
            if doc is not None and doc.content_fingerprint == fingerprint:
                continue
            if doc is None:
                doc = self.db.add(Document(corpus_id=corpus_id, external_id=record.id))
            for version in self.db.all(DocumentVersion):
                if version.document_id == doc.id and version.status == "active":
                    version.status = "inactive"
            doc.content_fingerprint = fingerprint
            self.db.add(DocumentVersion(document_id=doc.id, status="active"))
        return {"status": "completed"}

    def deps(self, **kw: Any) -> LearnDeps:
        def fetcher(source: WebsiteSource) -> WebsiteFetcher:
            return WebsiteFetcher(
                self.settings,
                origin=source.origin,
                scope=source.scope,
                transport=self.transport,
                allow_private=False,
            )

        async def room(session: Any, corpus: Any) -> int | None:
            return self.room

        base = {
            "settings": self.settings,
            "session_factory": self.db.factory(),
            "fetcher": fetcher,
            "ingest": self.ingest,
            "vector_store": self.vector_store,
            "room": room,
            "clock": lambda: self.clock,
        }
        return LearnDeps(**{**base, **kw})

    def pages(self) -> dict[str, WebsitePage]:
        return {p.url: p for p in self.db.all(WebsitePage)}

    @property
    def fetched(self) -> list[str]:
        return [u for u in self.transport.urls if not u.endswith(("/robots.txt", ".xml"))]


class _Vectors:
    def __init__(self) -> None:
        self.deleted: list[dict[str, Any]] = []

    async def delete_by_filter(self, collection: str, filters: dict[str, Any]) -> None:
        self.deleted.append(filters)


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    async def nothing(corpus: Any) -> None:
        return None

    monkeypatch.setattr(archive_mod, "_invalidate_cache", nothing)


def flat_site(n: int = 25, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    names = [f"p{i}" for i in range(n)]
    pages: dict[str, Any] = {
        "/": long_page("Home", "home", links=tuple(f"/{x}" for x in names)),
    }
    for name in names:
        pages[f"/{name}"] = long_page(name, name)
    pages.update(extra or {})
    return pages


async def test_best_first_under_a_cap_reads_the_important_pages() -> None:
    names = ["faq", "about", "contact", "doctors", "pricing", "services"] + [
        f"p{i}" for i in range(24)
    ]
    pages = {"/": long_page("Home", "home", links=tuple(f"/{x}" for x in names))}
    for name in names:
        pages[f"/{name}"] = long_page(name, name)
    w = World(pages, plan={"website_pages_max": 5})
    result = await learn_source(w.source.id, deps=w.deps())
    assert result.status == "done"
    assert sorted(w.fetched) == sorted(
        f"{ORIGIN}/{x}" for x in ("", "faq", "about", "contact", "doctors")
    )
    rows = w.pages()
    assert sum(1 for p in rows.values() if p.status == "learned") == 5
    left = [p for p in rows.values() if p.skip_reason == "over_limit"]
    assert len(left) == 26 and all(p.status == "skipped" for p in left)
    assert w.source.status == "ready" and w.source.pages_learned == 5


async def test_sitemap_pages_are_found_and_nofollow_pages_hide_their_links() -> None:
    sitemap = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://shreedental.in/only-in-sitemap</loc><priority>0.9</priority></url>"
        "</urlset>"
    )
    pages = {
        "/": long_page("Home", "home", links=("/a",)),
        "/a": long_page(
            "A", "a", head='<meta name="robots" content="nofollow">', links=("/hidden",)
        ),
        "/hidden": long_page("Hidden", "hidden"),
        "/only-in-sitemap": long_page("Only in sitemap", "sm"),
        "/sitemap.xml": sitemap,
    }
    w = World(pages)
    await learn_source(w.source.id, deps=w.deps())
    rows = w.pages()
    assert rows[f"{ORIGIN}/only-in-sitemap"].status == "learned"
    assert rows[f"{ORIGIN}/only-in-sitemap"].in_sitemap is True
    assert f"{ORIGIN}/hidden" not in rows
    assert rows[f"{ORIGIN}/a"].status == "learned"


async def test_noindex_default_excludes_and_owner_patterns() -> None:
    pages = {
        "/": long_page("Home", "home", links=("/secret", "/cart", "/blog/post", "/services")),
        "/secret": long_page("Secret", "s", head='<meta name="robots" content="noindex">'),
        "/cart": long_page("Cart", "c"),
        "/blog/post": long_page("Post", "b"),
        "/services": long_page("Services", "sv"),
    }
    w = World(pages)
    w.db.add(
        WebsiteSettings(corpus_id=w.corpus.id, tenant_id=w.tenant.id, exclude_patterns=["/blog/*"])
    )
    await learn_source(w.source.id, deps=w.deps())
    rows = w.pages()
    assert (rows[f"{ORIGIN}/secret"].status, rows[f"{ORIGIN}/secret"].skip_reason) == (
        "skipped",
        "noindex",
    )
    assert (rows[f"{ORIGIN}/cart"].status, rows[f"{ORIGIN}/cart"].skip_reason) == (
        "skipped",
        "private_page",
    )
    assert rows[f"{ORIGIN}/blog/post"].status == "excluded"
    assert f"{ORIGIN}/cart" not in w.fetched and f"{ORIGIN}/blog/post" not in w.fetched
    ids = {r.id for batch in w.batches for r in batch}
    assert ids == {f"web:{ORIGIN}/", f"web:{ORIGIN}/services"}


async def test_index_html_is_the_home_page_and_near_duplicates_are_skipped() -> None:
    original = words(400, "svc")
    near = original.replace("svc200", "changed")
    pages = {
        "/": long_page("Home", "home", links=("/index.html", "/services", "/services-2")),
        "/services": html_page("Services", original),
        "/services-2": html_page("Services", near),
    }
    w = World(pages)
    await learn_source(w.source.id, deps=w.deps())
    rows = w.pages()
    ids = [r.id for batch in w.batches for r in batch]
    assert ids.count(f"web:{ORIGIN}/") == 1
    dup = rows[f"{ORIGIN}/services-2"]
    assert (dup.status, dup.skip_reason) == ("skipped", "duplicate")
    assert dup.duplicate_of == rows[f"{ORIGIN}/services"].id


async def test_records_are_batched_with_stable_metadata_and_progress_is_saved() -> None:
    w = World(flat_site(25))
    await learn_source(w.source.id, deps=w.deps())
    assert [len(b) for b in w.batches] == [10, 10, 6]
    for record in (r for b in w.batches for r in b):
        assert record.id.startswith(f"web:{ORIGIN}/")
        assert record.markdown is True
        assert record.metadata == {"source": "website", "site": "shreedental.in"}
        assert "# " in record.text
    jobs = w.db.all(IngestJob)
    assert [j.mode for j in jobs] == ["website"] * 3
    assert [j.total_documents for j in jobs] == [10, 10, 6]
    # the site's counters grow while it is read (a heartbeat the owner can watch)
    assert w.snapshots[0][1] == 0 and w.snapshots[1][1] == 10 and w.snapshots[2][1] == 20
    assert w.snapshots[1][0] >= 26 - 6
    assert w.source.pages_found == 26 and w.source.pages_learned == 26


async def test_document_limit_stops_the_run_and_says_why() -> None:
    w = World(flat_site(10))
    w.room = 3
    await learn_source(w.source.id, deps=w.deps())
    rows = w.pages()
    assert sum(1 for p in rows.values() if p.status == "learned") == 3
    assert all(p.skip_reason == "over_limit" for p in rows.values() if p.status == "skipped")
    assert w.source.last_error_code == "document_limit"
    assert len(w.db.all(Document)) == 3


async def test_a_second_start_while_a_run_is_open_fetches_nothing() -> None:
    w = World(flat_site(3))
    w.db.add(
        WebsiteRun(
            source_id=w.source.id,
            corpus_id=w.corpus.id,
            kind="first",
            trigger="manual",
            started_at=NOW,
            heartbeat_at=NOW - timedelta(minutes=2),
            counts={},
        )
    )
    result = await learn_source(w.source.id, kind="full", deps=w.deps())
    assert result.status == "busy"
    assert w.transport.requests == []


async def test_a_run_without_a_heartbeat_for_15_minutes_is_closed_and_replaced() -> None:
    w = World(flat_site(3))
    stale = w.db.add(
        WebsiteRun(
            source_id=w.source.id,
            corpus_id=w.corpus.id,
            kind="first",
            trigger="manual",
            started_at=NOW - timedelta(hours=1),
            heartbeat_at=NOW - timedelta(minutes=16),
            counts={},
        )
    )
    result = await learn_source(w.source.id, deps=w.deps())
    assert result.status == "done"
    assert stale.status == "failed" and stale.finished_at is not None
    assert stale.error_code == "stalled"


async def test_the_site_disappearing_mid_run_stops_quietly() -> None:
    w = World(flat_site(25))
    w.on_ingest = lambda: w.db.rows[WebsiteSource].clear()
    result = await learn_source(w.source.id, deps=w.deps())
    assert result.status == "stopped"
    assert len(w.batches) == 1


async def test_first_pass_sends_one_email_and_a_re_read_sends_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[tuple[str, str, dict[str, Any]]] = []

    def fake_enqueue(template: str, to: str, context: Any, *, expires_s: Any) -> None:
        sent.append((template, to, dict(context)))

    monkeypatch.setattr(learn_mod, "enqueue_email", fake_enqueue)
    w = World(flat_site(12))
    owner = w.db.add(User(email="asha@shreedental.in", name="Asha", password_hash="x"))
    member = w.db.add(User(email="staff@shreedental.in", name="Ravi", password_hash="x"))
    w.db.add(TenantMember(tenant_id=w.tenant.id, user_id=owner.id, role="owner"))
    w.db.add(TenantMember(tenant_id=w.tenant.id, user_id=member.id, role="member"))
    await learn_source(w.source.id, deps=w.deps())
    assert [(t, to) for t, to, _ in sent] == [("website_learned", "asha@shreedental.in")]
    context = sent[0][2]
    assert context["business_name"] == "Shree Dental Clinic"
    assert context["site"] == "shreedental.in" and context["pages_learned"] == 13
    assert context["knowledge_url"].endswith("/knowledge")
    await learn_source(w.source.id, kind="full", deps=w.deps())
    assert len(sent) == 1


async def test_an_unchanged_site_costs_no_ingest_on_a_re_read() -> None:
    w = World(flat_site(12))
    await learn_source(w.source.id, deps=w.deps())
    first = len(w.batches)
    pages_before = {u: p.document_id for u, p in w.pages().items()}
    result = await learn_source(w.source.id, kind="full", deps=w.deps())
    assert result.status == "done" and len(w.batches) == first
    assert result.counts["unchanged"] == 13
    assert {u: p.document_id for u, p in w.pages().items()} == pages_before
    assert all(p.status == "learned" for p in w.pages().values())


async def test_a_changed_page_is_ingested_again_and_counted() -> None:
    w = World(flat_site(4))
    await learn_source(w.source.id, deps=w.deps())
    w.transport.pages["/p1"] = long_page("p1", "rewritten")
    await learn_source(w.source.id, kind="full", deps=w.deps())
    assert [r.id for r in w.batches[-1]] == [f"web:{ORIGIN}/p1"]
    page = w.pages()[f"{ORIGIN}/p1"]
    assert page.change_count == 1 and page.status == "learned"


async def test_a_failed_batch_marks_its_pages_and_the_site() -> None:
    w = World(flat_site(3))
    w.fail_ingest = True
    result = await learn_source(w.source.id, deps=w.deps())
    assert result.status == "failed"
    assert all(
        p.status == "failed" and p.skip_reason == "ingest_failed" for p in w.pages().values()
    )
    assert w.source.status == "failed" and w.source.last_error_code == "ingest_failed"


async def test_a_private_start_page_blocks_the_site(monkeypatch: pytest.MonkeyPatch) -> None:
    from callobuzz_rag.website import netguard

    async def private(host: str, port: int) -> list[str]:
        return ["10.0.0.7"]

    monkeypatch.setattr(netguard, "resolve_host", private)
    w = World(flat_site(2))
    result = await learn_source(w.source.id, deps=w.deps())
    assert result.status == "blocked"
    assert w.source.status == "blocked" and w.source.last_error_code == "website_blocked"
    assert w.transport.requests == []


async def test_policy_off_pauses_the_site_and_reads_nothing() -> None:
    w = World(flat_site(2), plan={"website_learning_enabled": False})
    result = await learn_source(w.source.id, deps=w.deps())
    assert result.status == "disabled" and result.reason == "plan"
    assert w.source.status == "paused" and w.source.pause_reason == "plan"
    assert w.transport.requests == []


async def test_an_origin_removed_site_stays_paused() -> None:
    w = World(flat_site(2))
    w.source.status, w.source.pause_reason = "paused", "origin_removed"
    result = await learn_source(w.source.id, deps=w.deps())
    assert result.status == "paused" and w.transport.requests == []


async def test_a_page_that_turns_noindex_is_archived() -> None:
    w = World(flat_site(3))
    await learn_source(w.source.id, deps=w.deps())
    w.transport.pages["/p2"] = long_page("p2", "p2", head='<meta name="robots" content="noindex">')
    await learn_source(w.source.id, kind="full", deps=w.deps())
    page = w.pages()[f"{ORIGIN}/p2"]
    assert (page.status, page.skip_reason) == ("skipped", "noindex")
    assert len(w.vector_store.deleted) == 1
    version = next(v for v in w.db.all(DocumentVersion) if v.document_id == page.document_id)
    assert version.status == "inactive"


async def test_learn_urls_reads_only_those_pages_conditionally() -> None:
    w = World(flat_site(3))
    await learn_source(w.source.id, deps=w.deps())
    page = w.pages()[f"{ORIGIN}/p1"]
    page.etag = '"v1"'
    w.transport.requests.clear()
    seen: dict[str, str] = {}

    def not_modified(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(304)

    w.transport.pages["/p1"] = not_modified
    batches = len(w.batches)
    result = await learn_urls(
        w.source.id, [f"{ORIGIN}/p1", "https://evil.example/x"], kind="page", deps=w.deps()
    )
    assert result.status == "done" and result.counts["unchanged"] == 1
    assert seen["if-none-match"] == '"v1"'
    assert [u for u in w.fetched] == [f"{ORIGIN}/p1"]
    assert len(w.batches) == batches
    kinds = [r.kind for r in w.db.all(WebsiteRun)]
    assert kinds == ["first", "page"]


async def test_learn_urls_ingests_a_new_page_inside_the_cap() -> None:
    w = World({**flat_site(2), "/new": long_page("New", "fresh")}, plan={"website_pages_max": 3})
    await learn_source(w.source.id, deps=w.deps())
    assert w.source.pages_learned == 3
    await learn_urls(w.source.id, [f"{ORIGIN}/new"], deps=w.deps())
    page = w.pages().get(f"{ORIGIN}/new")
    assert page is not None and (page.status, page.skip_reason) == ("skipped", "over_limit")


async def test_the_canonical_link_decides_the_stored_address() -> None:
    pages = {
        "/": long_page("Home", "home", links=("/old-url",)),
        "/old-url": long_page("Old", "same", head='<link rel="canonical" href="/new-url">'),
    }
    w = World(pages)
    await learn_source(w.source.id, deps=w.deps())
    rows = w.pages()
    assert rows[f"{ORIGIN}/new-url"].status == "learned"
    assert (rows[f"{ORIGIN}/old-url"].status, rows[f"{ORIGIN}/old-url"].skip_reason) == (
        "skipped",
        "duplicate",
    )
    ids = [r.id for b in w.batches for r in b]
    assert f"web:{ORIGIN}/new-url" in ids and f"web:{ORIGIN}/old-url" not in ids


async def test_thin_pages_files_and_failed_pages_are_explained() -> None:
    pdf = httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
    pages = {
        "/": long_page("Home", "home", links=("/thin", "/brochure.pdf", "/gone", "/team")),
        "/thin": html_page("Thin", "Only a few words here."),
        "/brochure.pdf": pdf,
        "/gone": httpx.Response(404),
        "/team": long_page("Team", "team"),
    }
    w = World(pages)
    result = await learn_source(w.source.id, deps=w.deps())
    rows = w.pages()
    assert rows[f"{ORIGIN}/thin"].skip_reason == "thin"
    assert (
        f"{ORIGIN}/brochure.pdf" not in rows or rows[f"{ORIGIN}/brochure.pdf"].status != "learned"
    )
    assert (rows[f"{ORIGIN}/gone"].status, rows[f"{ORIGIN}/gone"].http_status) == ("failed", 404)
    assert rows[f"{ORIGIN}/team"].status == "learned"
    assert result.counts["files_found"] >= 1


# ------------------------------------------------------------------ review fixes


def active_versions(w: World, page: WebsitePage) -> list[DocumentVersion]:
    return [
        v
        for v in w.db.all(DocumentVersion)
        if v.document_id == page.document_id and v.status == "active"
    ]


async def test_a_page_learned_again_after_it_was_archived_has_an_active_version() -> None:
    w = World(flat_site(3))
    await learn_source(w.source.id, deps=w.deps())
    url = f"{ORIGIN}/p2"
    text = long_page("p2", "p2")
    w.transport.pages["/p2"] = long_page("p2", "p2", head='<meta name="robots" content="noindex">')
    await learn_source(w.source.id, kind="full", deps=w.deps())
    assert w.pages()[url].status == "skipped" and not active_versions(w, w.pages()[url])
    w.transport.pages["/p2"] = text  # the very same text as the first time
    await learn_source(w.source.id, kind="full", deps=w.deps())
    page = w.pages()[url]
    assert page.status == "learned" and len(active_versions(w, page)) == 1


@pytest.mark.parametrize(
    "target,reason",
    [("/login", "private_page"), ("/blog/x", "pattern"), ("/hidden", "robots_disallowed")],
)
async def test_a_canonical_target_the_site_leaves_out_is_not_learned(
    target: str, reason: str
) -> None:
    pages = {
        "/": long_page("Home", "home", links=("/members",)),
        "/members": long_page("Members", "m", head=f'<link rel="canonical" href="{target}">'),
        target: long_page("Target", "t"),
        "/robots.txt": "User-agent: *\nDisallow: /hidden\n",
    }
    w = World(pages)
    w.db.add(
        WebsiteSettings(corpus_id=w.corpus.id, tenant_id=w.tenant.id, exclude_patterns=["/blog/*"])
    )
    await learn_source(w.source.id, deps=w.deps())
    rows = w.pages()
    assert (rows[f"{ORIGIN}/members"].status, rows[f"{ORIGIN}/members"].skip_reason) == (
        "skipped",
        reason,
    )
    assert rows.get(f"{ORIGIN}{target}") is None or rows[f"{ORIGIN}{target}"].status != "learned"
    assert f"web:{ORIGIN}{target}" not in {r.id for b in w.batches for r in b}
    assert f"{ORIGIN}{target}" not in w.fetched


async def test_a_redirect_to_the_login_page_is_not_learned_as_the_login_page() -> None:
    def to_login(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/login?next=/members"})

    pages = {
        "/": long_page("Home", "home", links=("/members",)),
        "/members": to_login,
        "/login": long_page("Sign in", "login"),
    }
    w = World(pages)
    await learn_source(w.source.id, deps=w.deps())
    assert f"web:{ORIGIN}/login" not in {r.id for b in w.batches for r in b}
    assert w.pages()[f"{ORIGIN}/members"].status != "learned"


async def test_a_canonical_target_the_owner_left_out_is_not_learned() -> None:
    pages = {
        "/": long_page("Home", "home", links=("/a", "/b")),
        "/a": long_page("A", "a", head='<link rel="canonical" href="/b">'),
        "/b": long_page("B", "b"),
    }
    w = World(pages)
    w.db.add(
        WebsitePage(
            source_id=w.source.id,
            corpus_id=w.corpus.id,
            url=f"{ORIGIN}/b",
            url_hash=learn_mod.url_hash(f"{ORIGIN}/b"),
            status="excluded",
            skip_reason="owner",
            excluded_by_owner=True,
        )
    )
    await learn_source(w.source.id, deps=w.deps())
    rows = w.pages()
    assert (rows[f"{ORIGIN}/a"].status, rows[f"{ORIGIN}/a"].skip_reason) == ("skipped", "owner")
    assert rows[f"{ORIGIN}/b"].status == "excluded"
    assert f"web:{ORIGIN}/b" not in {r.id for b in w.batches for r in b}


async def test_a_re_read_keeps_to_the_page_cap() -> None:
    first = {"/": long_page("Home", "home", links=tuple(f"/p{i}" for i in range(4)))}
    first.update({f"/p{i}": long_page(f"p{i}", f"p{i}") for i in range(4)})
    w = World(first, plan={"website_pages_max": 5})
    await learn_source(w.source.id, deps=w.deps())
    assert w.source.pages_learned == 5
    better = ("faq", "about", "contact", "doctors", "pricing")
    w.transport.pages["/"] = long_page(
        "Home", "home", links=tuple(f"/p{i}" for i in range(4)) + tuple(f"/{x}" for x in better)
    )
    w.transport.pages.update({f"/{x}": long_page(x, x) for x in better})
    await learn_source(w.source.id, kind="full", deps=w.deps())
    rows = w.pages()
    learned = [p for p in rows.values() if p.status == "learned"]
    assert len(learned) == 5 and w.source.pages_learned == 5
    old = [rows[f"{ORIGIN}/p{i}"] for i in range(4)]
    assert all(p.skip_reason == "over_limit" and not active_versions(w, p) for p in old)
    assert all(active_versions(w, p) for p in learned)


async def test_a_learned_page_that_must_no_longer_be_learned_leaves_the_answers() -> None:
    pages = {
        **flat_site(5),
        "/": long_page("Home", "home", links=tuple(f"/p{i}" for i in range(5))),
    }
    w = World(pages)
    await learn_source(w.source.id, deps=w.deps())
    w.transport.pages["/robots.txt"] = "User-agent: *\nDisallow: /p1\n"
    w.transport.pages["/p2"] = html_page("p2", "Now only a few words.")
    w.transport.pages["/p3"] = long_page("p3", "p3", head='<link rel="canonical" href="/p4">')
    await learn_source(w.source.id, kind="full", deps=w.deps())
    rows = w.pages()
    expected = {"p1": "robots_disallowed", "p2": "thin", "p3": "duplicate"}
    for name, reason in expected.items():
        page = rows[f"{ORIGIN}/{name}"]
        assert (page.status, page.skip_reason) == ("skipped", reason), name
        assert not active_versions(w, page), name
    assert active_versions(w, rows[f"{ORIGIN}/p0"])
    assert w.source.pages_learned == sum(1 for p in rows.values() if p.status == "learned")


async def test_a_failed_read_keeps_what_was_learned() -> None:
    w = World(flat_site(3))
    await learn_source(w.source.id, deps=w.deps())
    w.transport.pages["/p1"] = httpx.Response(500)
    await learn_source(w.source.id, kind="full", deps=w.deps())
    page = w.pages()[f"{ORIGIN}/p1"]
    assert page.status == "failed" and active_versions(w, page)
