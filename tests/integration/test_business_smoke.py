"""The business path end to end on real PostgreSQL and Redis, against a local website (BIZ5).

One test, in the order a clinic owner lives it:

 1. a new owner signs in and sets up from the website address (knowledge base, published
    assistant, widget key, a website source);
 2. before the first read, a visitor's question gets the polite "still learning" reply;
 3. the site is read: four pages learned, `/cart` (a default exclusion) and `/private/x`
    (robots.txt) never requested, one `website_learned` e-mail queued;
 4. the widget loads from the site's origin and onboarding says connected;
 5. a question about root canals now goes through POST /widget/chat: the "still learning" reply
    has stopped and the answer's sources carry the learned root canal page (the stub embedder,
    the in-memory vector store and a canned local LLM stand in for the models; the wording of
    the answer is not what this proves; no paid call is made);
 6. the site changes `/pricing` and pushes it: one re-read, a new document version; the scheduler
    then checks the unchanged pages by a conditional request: no new version, no embedding;
 7. the site deletes `/contact-us`: two checks an hour apart archive it and it leaves the search;
 8. the plan hides Advanced: the owner's session is refused on a key route, a `tk_` key is not.

Everything is served from 127.0.0.1 and nothing leaves the machine: the site's host name
(`shreedental.test`) is mapped to 127.0.0.1 inside this process only, and the engine is told
explicitly to allow private networks. Celery is not used: the tasks' own functions run inline.
Needs Docker (testcontainers); skipped with the integration suite when it is down. Sync test:
alembic's env.py calls asyncio.run().
"""

import asyncio
import hashlib
import math
import os
import re
import socket
import threading
import uuid
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from callobuzz_rag.embeddings.base import EmbeddingProvider, EmbeddingResult
from callobuzz_rag.llm.base import LLMProvider
from callobuzz_rag.llm.models import LLMMessage, LLMResponse
from callobuzz_rag.vectordb.base import SearchResult, VectorPoint, VectorStorageProvider
from tests.integration.conftest import VALKEY_IMAGE, _async_url
from tests.integration.test_accounts_flow import _alembic, _redis_url
from tests.integration.test_billing_month_e2e import _frames
from tests.unit.website_fixtures import words

# the assistant answers through the director (steps 2 and 5), so the autouse `_assistant_off`
# must not replace it (tests/conftest.py)
pytestmark = [pytest.mark.integration, pytest.mark.assistant_on]

HOST = "shreedental.test"
PASSWORD = "correct horse battery"
SECRET = "e" * 64
OWNER = "owner@shreedental.in"
DIMS = 64


def page(title: str, body: str, seed: str, links: tuple[str, ...] = ()) -> str:
    nav = "".join(f'<a href="{href}">{href}</a> ' for href in links)
    return (
        f"<!doctype html><html><head><title>{title}</title></head><body>"
        f"<header><nav>{nav}</nav></header><main><h1>{title}</h1><p>{body}</p>"
        f"<p>{words(120, seed)}</p></main></body></html>"
    )


class Site:
    """A website on 127.0.0.1 that answers conditional requests and records every request."""

    def __init__(self) -> None:
        self.files: dict[str, tuple[str, str, str]] = {}  # path -> (content type, etag, body)
        self.requests: list[tuple[str, str | None]] = []  # (path, If-None-Match)
        self.gone: set[str] = set()
        self._server: ThreadingHTTPServer | None = None

    def put(self, path: str, body: str, kind: str = "text/html; charset=utf-8") -> None:
        self.files[path] = (kind, '"' + hashlib.md5(body.encode()).hexdigest() + '"', body)

    @property
    def port(self) -> int:
        assert self._server is not None
        return int(self._server.server_address[1])

    def paths(self, since: int = 0) -> list[str]:
        return [p for p, _ in self.requests[since:]]

    def start(self) -> None:
        site = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                path = urlsplit(self.path).path
                inm = self.headers.get("If-None-Match")
                site.requests.append((path, inm))
                entry = site.files.get(path)
                if entry is None or path in site.gone:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                kind, etag, body = entry
                if inm == etag:
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    self.end_headers()
                    return
                data = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("ETag", etag)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()


class StubEmbedder(EmbeddingProvider):
    """A bag of hashed words: pages that share words with a question are near it. Counts calls."""

    def __init__(self) -> None:
        self.calls = 0

    def vector(self, value: str) -> list[float]:
        out = [0.0] * DIMS
        for token in re.findall(r"[a-z0-9]+", value.lower()):
            out[int(hashlib.md5(token.encode()).hexdigest(), 16) % DIMS] += 1.0
        norm = math.sqrt(sum(x * x for x in out)) or 1.0
        return [x / norm for x in out]

    async def embed_texts(
        self, texts: list[str], task: str = "retrieval.passage"
    ) -> list[EmbeddingResult]:
        self.calls += len(texts)
        return [
            EmbeddingResult(self.vector(t), "stub", "1", DIMS, token_count=len(t.split()))
            for t in texts
        ]

    async def embed_query(self, text: str) -> EmbeddingResult:
        return EmbeddingResult(self.vector(text), "stub", "1", DIMS)

    async def health_check(self) -> bool:
        return True


ANSWER = "A root canal at Shree Dental Clinic costs Rs 4,500 and takes two visits."


class StubLLM(LLMProvider):
    """A canned local model: the turn label is a business question, the answer one sentence
    that the learned page supports. Nothing leaves the machine."""

    def __init__(self) -> None:
        self.streams = 0

    async def complete(self, messages: list[LLMMessage], **kwargs: Any) -> LLMResponse:
        return LLMResponse(content=ANSWER, model="stub-model")

    async def complete_stream(self, messages: list[LLMMessage], **kwargs: Any) -> Any:
        self.streams += 1
        for word in ANSWER.split(" "):
            yield word + " "

    async def complete_json(self, messages: list[LLMMessage], **kwargs: Any) -> LLMResponse:
        label = '{"label": "business_question", "confidence": 0.95, "language": "en"}'
        return LLMResponse(content=label, model="stub-model")

    async def health_check(self) -> bool:
        return True


class MemoryVectors(VectorStorageProvider):
    """Dense vectors in a dict. Filters are plain equality, which is all the pipeline sends."""

    def __init__(self) -> None:
        self.points: dict[str, dict[str, VectorPoint]] = {}

    async def create_collection(self, name: str, vector_size: int) -> None:
        self.points.setdefault(name, {})

    async def delete_collection(self, name: str) -> None:
        self.points.pop(name, None)

    async def upsert(self, collection: str, points: list[VectorPoint]) -> None:
        self.points.setdefault(collection, {}).update({p.id: p for p in points})

    def _match(self, point: VectorPoint, filters: dict[str, Any] | None) -> bool:
        return all(point.payload.get(k) == v for k, v in (filters or {}).items())

    async def search(
        self,
        collection: str,
        query_vector: list[float],
        limit: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        scored = [
            SearchResult(
                p.id,
                sum(a * b for a, b in zip(p.vector, query_vector, strict=False)),
                dict(p.payload),
            )
            for p in self.points.get(collection, {}).values()
            if p.vector and self._match(p, filters)
        ]
        return sorted(scored, key=lambda r: -r.score)[:limit]

    async def search_sparse(
        self,
        collection: str,
        sparse_vector: Any,
        limit: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        return []

    async def delete(self, collection: str, ids: list[str]) -> None:
        for point_id in ids:
            self.points.get(collection, {}).pop(point_id, None)

    async def delete_by_filter(self, collection: str, filters: dict[str, Any]) -> None:
        bucket = self.points.get(collection, {})
        for point_id in [i for i, p in bucket.items() if self._match(p, filters)]:
            del bucket[point_id]

    async def update_vectors(self, collection: str, points: list[VectorPoint]) -> None:
        return None

    async def update_payload(
        self, collection: str, filters: dict[str, Any], payload: dict[str, Any]
    ) -> None:
        for point in self.points.get(collection, {}).values():
            if self._match(point, filters):
                point.payload.update(payload)

    async def health_check(self) -> bool:
        return True


def _site_files(site: Site, base: str) -> None:
    home_links = ("/pricing", "/treatments/root-canal", "/contact-us", "/cart", "/private/x")
    site.put(
        "/",
        page(
            "Shree Dental Clinic, Pune",
            "Family dentistry in Pune, Mon-Sat 10:00-19:00.",
            "home",
            home_links,
        ),
    )
    site.put("/pricing", page("Pricing", "Check-up Rs 500, cleaning Rs 1,200.", "price"))
    site.put(
        "/treatments/root-canal",
        page(
            "Root canal treatment",
            "A root canal at Shree Dental Clinic costs Rs 4,500 and takes two visits. "
            "The root canal charge includes the crown consultation.",
            "endo",
        ),
    )
    site.put("/contact-us", page("Contact us", "Call +91 98220 12345 or WhatsApp us.", "contact"))
    site.put("/cart", page("Cart", "Your cart is empty.", "cart"))
    site.put("/private/x", page("Private", "Staff only.", "private"))
    site.put("/robots.txt", "User-agent: *\nDisallow: /private/\n", "text/plain")
    site.put(
        "/sitemap.xml",
        '<?xml version="1.0"?><urlset '
        'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(
            f"<url><loc>{base}{p}</loc></url>"
            for p in ("/", "/pricing", "/treatments/root-canal", "/contact-us", "/private/x")
        )
        + "</urlset>",
        "application/xml",
    )


async def _rows(factory: Any, sql: str, **params: Any) -> list[Any]:
    async with factory() as session:
        return [tuple(r) for r in (await session.execute(text(sql), params)).all()]


async def _flow(db_url: str, redis_url: str) -> None:
    import callobuzz_rag.website.push as push_mod
    import callobuzz_rag.website.service as service_mod
    from callobuzz_rag.accounts.passwords import hash_password
    from callobuzz_rag.accounts.ratelimit import AccountRateLimiter
    from callobuzz_rag.api.app import create_app
    from callobuzz_rag.chunking.chunker import chunker_from_settings
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.db.models.tenant import Tenant
    from callobuzz_rag.db.models.tenant_member import TenantMember
    from callobuzz_rag.db.models.user import User
    from callobuzz_rag.enrichment.extractive import ExtractiveEnrichmentProvider
    from callobuzz_rag.pipeline.orchestrator import IngestionOrchestrator
    from callobuzz_rag.retrieval.context_assembly import ContextAssembler
    from callobuzz_rag.retrieval.safety import ContentSanitizer
    from callobuzz_rag.website.check import check_pages, schedule_checks
    from callobuzz_rag.website.fetch import WebsiteFetcher
    from callobuzz_rag.website.learn import LearnDeps, learn_source, learn_urls
    from callobuzz_rag.website.urls import external_id

    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        session_secret=SECRET,  # type: ignore[arg-type]
        email_provider="log",
        admin_api_key="admin-key-business-smoke",  # type: ignore[arg-type]
        # `redis_url` is a property built from these, so the container's address goes here
        redis_host=urlsplit(redis_url).hostname,
        redis_port=urlsplit(redis_url).port,
        website_allow_private_networks=True,
        website_request_delay_ms=0,
    )
    site = Site()
    site.start()
    base = f"http://{HOST}:{site.port}"
    _site_files(site, base)
    embedder, vectors, llm = StubEmbedder(), MemoryVectors(), StubLLM()
    learn_calls: list[tuple[str, str, str]] = []
    url_calls: list[tuple[str, list[str], str, str]] = []
    emails: list[tuple[str, str]] = []
    real_getaddrinfo = socket.getaddrinfo

    def fake_getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        name = host.decode() if isinstance(host, bytes) else host
        if isinstance(name, str) and name.endswith(HOST):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", int(port or 80)))]
        return real_getaddrinfo(host, port, *args, **kwargs)

    def dispatch_learn(source_id: Any, kind: str, trigger: str) -> bool:
        learn_calls.append((str(source_id), kind, trigger))
        return True

    def dispatch_urls_service(source_id: Any, urls: Any, trigger: str = "manual") -> bool:
        url_calls.append((str(source_id), list(urls), "page", trigger))
        return True

    def dispatch_urls_push(source_id: Any, urls: Any, kind: str, trigger: str) -> bool:
        url_calls.append((str(source_id), list(urls), kind, trigger))
        return True

    try:
        with (
            patch.dict(
                os.environ, {"RAG_ENABLE_CONTENT_PORTAL": "false", "NO_PROXY": "*", "no_proxy": "*"}
            ),
            patch.object(socket, "getaddrinfo", fake_getaddrinfo),
            patch(
                "callobuzz_rag.llm.litellm_provider.LiteLLMProvider.from_resolved_config",
                lambda *a, **k: llm,
            ),
            patch.object(service_mod, "dispatch_learn", dispatch_learn),
            patch.object(service_mod, "dispatch_urls", dispatch_urls_service),
            patch.object(push_mod, "dispatch_urls", dispatch_urls_push),
            patch(
                "callobuzz_rag.website.learn.enqueue_email",
                lambda template, to, context, **kw: emails.append((template, to)),
            ),
        ):
            app = create_app()
            app.state.settings = settings
            app.state.session_factory = factory
            app.state.vector_store = vectors
            app.state.embedder = embedder
            app.state.context_assembler = ContextAssembler(
                settings, sanitizer=ContentSanitizer(settings)
            )
            limiter = AccountRateLimiter(redis_url)
            app.state.account_rate_limiter = limiter

            async with factory() as session:
                plan_id = (
                    await session.execute(text("SELECT id FROM plans WHERE is_default"))
                ).scalar_one()
                tenant = Tenant(name="Shree Dental Clinic, Pune", api_key_hash="")
                tenant.plan_id = plan_id
                session.add(tenant)
                await session.flush()
                user = User(
                    email=OWNER,
                    name="Dr. Meera Shah",
                    password_hash=await hash_password(PASSWORD),
                    email_verified_at=text("now()"),
                )
                session.add(user)
                await session.flush()
                session.add(TenantMember(tenant_id=tenant.id, user_id=user.id, role="owner"))
                await session.commit()

            async def ingest(batch: list[Any], corpus_id: uuid.UUID, job_id: uuid.UUID) -> Any:
                from callobuzz_rag.accounts.caps import ensure_document_room_for_job

                async with factory() as session:
                    collection = (
                        await session.execute(
                            text("SELECT qdrant_collection FROM corpora WHERE id = :c"),
                            {"c": corpus_id},
                        )
                    ).scalar_one()
                    orchestrator = IngestionOrchestrator(
                        session=session,
                        storage=None,  # type: ignore[arg-type]
                        parser=None,  # type: ignore[arg-type]
                        chunker=chunker_from_settings(settings),
                        enricher=ExtractiveEnrichmentProvider(),
                        embedder=embedder,
                        vector_store=vectors,
                        bucket="unused",
                        collection=collection,
                        settings=settings,
                        document_room=ensure_document_room_for_job,
                    )
                    outcome = await orchestrator.ingest_records(batch, corpus_id, job_id)
                return {"job_id": str(job_id), **asdict(outcome)}

            deps = LearnDeps(
                settings=settings,
                session_factory=factory,
                fetcher=lambda s: WebsiteFetcher(settings, origin=s.origin, scope=s.scope),
                ingest=ingest,
                vector_store=vectors,
            )

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                login = await c.post(
                    "/api/v1/auth/login", json={"email": OWNER, "password": PASSWORD}
                )
                assert login.status_code == 200, login.text
                auth = {"Authorization": f"Bearer {login.json()['access_token']}"}

                # 1. set up from the website address
                started = await c.post(
                    "/api/v1/onboarding/website",
                    json={"website": base, "assistant_name": "Shree Dental Assistant"},
                    headers=auth,
                )
                assert started.status_code == 200, started.text
                state = started.json()
                kb_id, key_id = state["kb_id"], state["widget_key_id"]
                assert kb_id and key_id and "data-key=" in state["snippet"]
                assert state["connection"]["status"] == "waiting"
                public_key = (
                    await _rows(
                        factory,
                        "SELECT public_key FROM widget_keys WHERE id = :k",
                        k=uuid.UUID(key_id),
                    )
                )[0][0]
                sources = await _rows(
                    factory,
                    "SELECT id, scope, status FROM website_sources WHERE corpus_id = :c",
                    c=uuid.UUID(kb_id),
                )
                assert [(s[1], s[2]) for s in sources] == [(HOST, "discovering")]
                source_id = sources[0][0]
                assert learn_calls and learn_calls[0][0] == str(source_id)
                assistant = await c.get(f"/api/v1/corpora/{kb_id}/assistant", headers=auth)
                assert assistant.status_code == 200 and assistant.json()["published"] is not None

                # 2. before the first read: a polite "still learning"
                widget = {"X-Widget-Key": public_key, "Origin": base}
                early = await c.post(
                    "/api/v1/widget/chat",
                    json={"message": "What are the charges for a root canal?"},
                    headers=widget,
                )
                assert early.status_code == 200, early.text
                assert "still learning" in early.text
                early_done = [d for e, d in _frames(early.text) if e == "done"]
                assert early_done and early_done[0]["route"] == "handoff", early.text
                assert llm.streams == 0  # a still-learning turn never reaches the model

                # 3. the first read: four pages, nothing it should not touch, one e-mail
                result = await learn_source(
                    source_id, kind="first", trigger="onboarding", deps=deps
                )
                assert result.status == "done", result
                pages = await _rows(
                    factory,
                    "SELECT url, status FROM website_pages WHERE source_id = :s ORDER BY url",
                    s=source_id,
                )
                learned = {urlsplit(u).path for u, st in pages if st == "learned"}
                assert learned == {"/", "/pricing", "/treatments/root-canal", "/contact-us"}, pages
                requested = set(site.paths())
                assert "/cart" not in requested and "/private/x" not in requested
                assert [e[0] for e in emails] == ["website_learned"]
                assert emails[0][1] == OWNER

                # 4. the widget loads from the site: onboarding says connected
                config = await c.get("/api/v1/widget/config", headers=widget)
                assert config.status_code == 200, config.text
                onboarding = await c.get("/api/v1/onboarding", headers=auth)
                assert onboarding.json()["connection"]["status"] == "connected"
                assert onboarding.json()["learning"]["pages_learned"] == 4

                # 5. a root canal question goes through the widget: it is answered now, from the
                # learned page, and the "still learning" reply has stopped
                collection = (
                    await _rows(
                        factory,
                        "SELECT qdrant_collection FROM corpora WHERE id = :c",
                        c=uuid.UUID(kb_id),
                    )
                )[0][0]
                query = await embedder.embed_query("What is the charge for a root canal?")
                top = (await vectors.search(collection, query.vector, limit=3))[0]
                found = await _rows(
                    factory,
                    "SELECT external_id FROM documents WHERE id = :d",
                    d=uuid.UUID(top.payload["document_id"]),
                )
                assert found == [(external_id(f"{base}/treatments/root-canal"),)]
                answered = await c.post(
                    "/api/v1/widget/chat",
                    json={"message": "What is the charge for a root canal?"},
                    headers=widget,
                )
                assert answered.status_code == 200, answered.text
                assert "still learning" not in answered.text
                frames = _frames(answered.text)
                done = [d for e, d in frames if e == "done"]
                assert done and done[0]["route"] != "handoff", answered.text
                sources = [src for e, d in frames if e == "sources" for src in d["sources"]]
                assert sources, answered.text  # the answer shows where it came from
                shown = " ".join(f"{x['file_name']} {x['snippet']}" for x in sources).lower()
                assert "root canal" in shown, sources
                assert llm.streams >= 1

                async def versions(path: str) -> int:
                    rows = await _rows(
                        factory,
                        "SELECT count(*) FROM document_versions v JOIN documents d "
                        "ON d.id = v.document_id WHERE d.corpus_id = :c AND d.external_id = :e",
                        c=uuid.UUID(kb_id),
                        e=external_id(base + path),
                    )
                    return int(rows[0][0])

                # 6. a push re-reads one page; the scheduler's checks of the rest cost nothing
                made = await c.post(f"/api/v1/corpora/{kb_id}/website/learn-key", headers=auth)
                assert made.status_code == 201, made.text
                site.put(
                    "/pricing", page("Pricing", "Check-up Rs 600, cleaning Rs 1,400.", "price2")
                )
                pushed = await c.post(
                    f"/api/v1/public/learn/{made.json()['key']}", json={"url": f"{base}/pricing"}
                )
                assert pushed.status_code == 202 and pushed.json()["accepted"] == 1, pushed.text
                assert len(url_calls) == 1
                before = await versions("/pricing")
                await learn_urls(
                    uuid.UUID(url_calls[0][0]),
                    url_calls[0][1],
                    kind=url_calls[0][2],
                    trigger=url_calls[0][3],
                    deps=deps,
                )
                assert await versions("/pricing") == before + 1

                t1 = datetime.now(UTC) + timedelta(days=2)
                due: list[tuple[str, list[str], str]] = []
                tick = await schedule_checks(
                    factory,
                    settings,
                    dispatch_check=lambda s, ids, trig: due.append((s, ids, trig)),
                    dispatch_learn=lambda *a: None,
                    now=t1,
                )
                assert tick["due"] == 4 and len(due) == 1, tick
                embeds, seen = embedder.calls, len(site.requests)
                counts = {
                    p: await versions(p)
                    for p in ("/", "/pricing", "/treatments/root-canal", "/contact-us")
                }
                checked = await check_pages(
                    uuid.UUID(due[0][0]),
                    [uuid.UUID(i) for i in due[0][1]],
                    trigger=due[0][2],
                    deps=replace(deps, clock=lambda: t1),
                )
                assert set(checked.outcomes.values()) == {"unchanged"}, checked.outcomes
                assert embedder.calls == embeds
                assert {p: await versions(p) for p in counts} == counts
                assert any(inm for p, inm in site.requests[seen:] if p == "/treatments/root-canal")

                # 7. the site deletes /contact-us: two checks an hour apart archive it
                contact = (
                    await _rows(
                        factory,
                        "SELECT id, document_id FROM website_pages "
                        "WHERE source_id = :s AND url LIKE '%/contact-us'",
                        s=source_id,
                    )
                )[0]
                site.gone.add("/contact-us")
                t2 = t1 + timedelta(days=1)
                first = await check_pages(
                    source_id,
                    [contact[0]],
                    trigger="schedule",
                    deps=replace(deps, clock=lambda: t2),
                )
                assert list(first.outcomes.values()) == ["failed"]
                second = await check_pages(
                    source_id,
                    [contact[0]],
                    trigger="schedule",
                    deps=replace(deps, clock=lambda: t2 + timedelta(minutes=61)),
                )
                assert list(second.outcomes.values()) == ["removed"]
                status = await _rows(
                    factory, "SELECT status FROM website_pages WHERE id = :p", p=contact[0]
                )
                assert status == [("removed",)]
                active = await _rows(
                    factory,
                    "SELECT count(*) FROM document_versions WHERE document_id = :d "
                    "AND status = 'active'",
                    d=contact[1],
                )
                assert active == [(0,)]
                everything = await vectors.search(
                    collection, (await embedder.embed_query("contact us call")).vector, limit=50
                )
                assert str(contact[1]) not in {h.payload.get("document_id") for h in everything}

                # 8. the plan hides Advanced: a session is refused, a tk_ key is not
                dev = await c.post(
                    "/api/v1/tenants/me/keys",
                    json={"name": "Developer", "permission": "full"},
                    headers=auth,
                )
                assert dev.status_code == 201, dev.text
                async with factory() as session:
                    await session.execute(
                        text(
                            "UPDATE plans SET dashboard_features = CAST(:f AS jsonb) WHERE id = :p"
                        ),
                        {"f": '{"advanced": false}', "p": plan_id},
                    )
                    await session.commit()
                refused = await c.post(
                    "/api/v1/tenants/me/keys",
                    json={"name": "Another", "permission": "read"},
                    headers=auth,
                )
                assert refused.status_code == 403, refused.text
                assert refused.json()["code"] == "feature_not_in_plan"
                allowed = await c.post(
                    "/api/v1/tenants/me/keys",
                    json={"name": "Another", "permission": "read"},
                    headers={"X-API-Key": dev.json()["api_key"]},
                )
                assert allowed.status_code == 201, allowed.text
            await limiter.close()
    finally:
        site.stop()
        await engine.dispose()


def test_the_business_path_on_postgres_and_a_local_website() -> None:
    with PostgresContainer("postgres:16-alpine") as pg, RedisContainer(VALKEY_IMAGE) as redis:
        url = _async_url(pg)
        _alembic(url, "head")
        asyncio.run(_flow(url, _redis_url(redis)))
