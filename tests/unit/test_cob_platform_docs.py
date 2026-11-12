"""Platform docs: what the super admin adds to COB's knowledge base survives an upgrade."""

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.cob.kb import RECORD_PREFIX, records_for
from callobuzz_rag.cob.platform import (
    BUILTIN,
    PLATFORM,
    allow_system_write,
    backfill_builtin_tag,
    doc_source,
    parse_audience,
    platform_metadata,
    refuse_builtin,
)
from callobuzz_rag.cob.types import Audience
from tests.unit.scope_harness import call, scoped_app
from tests.unit.test_cob_manifest import SCREEN, write
from tests.unit.test_cob_sync import Env, make_root
from tests.unit.test_cob_system_guards import PROTECTED, with_system


@pytest.fixture
async def env(tmp_path: Path) -> Env:
    root = make_root(tmp_path)
    write(
        root / "docs/cob/screens/other.md",
        SCREEN.replace("/widget", "/other").replace("Website widget", "Other"),
    )
    environment = Env(root)
    await environment.setup()
    from callobuzz_rag.db.models.cob_status import CobStatus

    environment.db.add(CobStatus(id=1, sync_state="idle", document_count=0))
    return environment


def test_every_builtin_record_is_tagged(env: Env) -> None:
    records = records_for(env.index)
    assert records and all(r.metadata["cob_source"] == BUILTIN for r in records)


async def test_a_sync_never_updates_or_deletes_a_platform_doc(env: Env, tmp_path: Path) -> None:
    await env.sync()
    env.store.ids.add("faq-support-hours")  # a platform doc: no system:cob/ prefix
    # A version bump plus a manifest change: one page disappears from the engine's docs.
    (env.root / "docs/cob/screens/other.md").unlink()
    index = env.reload()
    out = await env.sync(index=index)
    assert not out.failed and out.removed
    assert "faq-support-hours" in env.store.ids
    assert "faq-support-hours" not in env.store.removed
    assert all(i.startswith(RECORD_PREFIX) for i in env.store.removed)


async def test_an_embedding_rebuild_only_removes_builtin_docs(env: Env) -> None:
    from callobuzz_rag.db.models.corpus import Corpus

    await env.sync()
    env.store.ids.add("faq-support-hours")
    corpus = next(c for c in env.db.all(Corpus) if c.is_system)
    corpus.applied_embedding = {"provider": "local", "model": "old", "dimensions": 384}
    out = await env.sync(force=True)
    assert out.rebuilt
    assert "faq-support-hours" in env.store.ids and "faq-support-hours" not in env.store.removed


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "_Rows":
        return self

    def all(self) -> list[Any]:
        return self._rows


class _Session:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    async def execute(self, _stmt: Any) -> _Rows:
        return _Rows(self.rows)


async def test_backfill_tags_old_builtin_docs_and_leaves_platform_docs_alone() -> None:
    old = SimpleNamespace(external_id=f"{RECORD_PREFIX}screens/widget#", doc_metadata={"cob": "1"})
    tagged = SimpleNamespace(
        external_id=f"{RECORD_PREFIX}guide/flow#", doc_metadata={"cob_source": BUILTIN}
    )
    platform = SimpleNamespace(
        external_id="faq-1", doc_metadata={"cob_source": PLATFORM, "audience": "customer"}
    )
    upload = SimpleNamespace(external_id=None, doc_metadata=None)
    session = _Session([old, tagged, platform, upload])
    assert await backfill_builtin_tag(session, uuid.uuid4()) == 1  # type: ignore[arg-type]
    assert old.doc_metadata == {"cob": "1", "cob_source": BUILTIN}
    assert platform.doc_metadata == {"cob_source": PLATFORM, "audience": "customer"}
    assert upload.doc_metadata is None
    assert await backfill_builtin_tag(session, uuid.uuid4()) == 0  # type: ignore[arg-type]


def test_doc_source() -> None:
    assert doc_source(f"{RECORD_PREFIX}a/b#", None) == BUILTIN
    assert doc_source(None, {"cob_source": BUILTIN}) == BUILTIN
    assert doc_source("faq", {"cob_source": PLATFORM}) == PLATFORM
    assert doc_source(None, None) == PLATFORM


def test_a_builtin_doc_is_readonly_even_for_the_super_admin() -> None:
    with pytest.raises(APIError) as caught:
        refuse_builtin(f"{RECORD_PREFIX}a/b#", None)
    assert caught.value.status_code == 403 and caught.value.code == "cob_builtin_readonly"
    refuse_builtin("faq", {"cob_source": PLATFORM})


def _request(**state: Any) -> Any:
    return SimpleNamespace(state=SimpleNamespace(**state))


def test_platform_metadata_defaults_to_customer_and_cannot_be_spoofed() -> None:
    user = SimpleNamespace(id=uuid.uuid4())
    meta = platform_metadata(
        _request(user=user), {"cob_source": "builtin", "uploaded_by": "x", "cob": "1", "k": "v"}
    )
    assert meta == {
        "k": "v",
        "audience": "customer",
        "cob_source": PLATFORM,
        "uploaded_by": str(user.id),
    }
    assert platform_metadata(_request(user=user), {"audience": "admin"})["audience"] == "admin"


def test_the_audience_must_be_a_known_one() -> None:
    assert parse_audience(None) == Audience.CUSTOMER
    with pytest.raises(APIError) as caught:
        parse_audience("everyone")
    assert caught.value.status_code == 422 and caught.value.code == "invalid_audience"


def test_only_a_super_admin_session_may_write_to_the_system_knowledge_base() -> None:
    system = SimpleNamespace(is_system=True)
    user = SimpleNamespace(id=uuid.uuid4())
    allow_system_write(_request(auth_kind="session", is_admin=True, user=user), system)
    allow_system_write(_request(auth_kind="session", is_admin=True, user=user), SimpleNamespace())
    refused = [
        _request(auth_kind="api_key", is_admin=True),  # the admin key
        _request(auth_kind="session", is_admin=False, user=user, tenant=object()),  # a workspace
        _request(auth_kind="api_key", is_admin=False, tenant=object()),  # a tenant key
    ]
    for request in refused:
        with pytest.raises(APIError) as caught:
            allow_system_write(request, system)
        assert caught.value.status_code == 409
        assert caught.value.code == "system_resource_protected"


# ------------------------------------------------------------------ the write routes

REFUSED_PRINCIPALS = ("admin", "tk_a", "ck_a", "owner_a", "member_a")


def _doc(world: Any, corpus_id: uuid.UUID, *, external_id: str | None, meta: Any) -> Any:
    doc = SimpleNamespace(
        id=uuid.uuid4(),
        corpus_id=corpus_id,
        external_id=external_id,
        doc_metadata=meta,
        file_name="support.txt",
        versions=[],
    )
    world.session.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: doc))
    return doc


@pytest.mark.parametrize("key", REFUSED_PRINCIPALS)
async def test_nobody_but_a_super_admin_session_adds_records(key: str) -> None:
    with scoped_app() as (app, world):
        ids = with_system(world)
        body = {"corpus_id": str(ids.corpus), "records": [{"id": "hours", "text": "9 to 5"}]}
        r = await call(app, key, "POST", "/api/v1/ingest/records", json=body)
        assert r.status_code in (403, 409), (key, r.text)
        assert r.status_code != 409 or r.json()["code"] == PROTECTED


async def test_a_super_admin_session_adds_a_platform_record_with_audience_and_author() -> None:
    with scoped_app() as (app, world):
        ids = with_system(world)
        records = [
            {"id": "hours", "text": "9 to 5", "metadata": {"cob_source": "builtin"}},
            {"id": "price", "text": "Rs 999", "metadata": {"audience": "admin"}},
        ]
        body = {"corpus_id": str(ids.corpus), "records": records}
        task = MagicMock()
        with patch("callobuzz_rag.worker.tasks.ingest_records_task", task):
            r = await call(app, "sa", "POST", "/api/v1/ingest/records", json=body)
        assert r.status_code == 202, r.text
        sent = task.delay.call_args.kwargs["records"]
        by_id = {x["id"]: x["metadata"] for x in sent}
        assert by_id["hours"]["audience"] == "customer"  # the default
        assert by_id["price"]["audience"] == "admin"
        for meta in by_id.values():
            assert meta["cob_source"] == PLATFORM  # never taken from the request
            assert meta["uploaded_by"] == str(world.superadmin.id)


@pytest.mark.parametrize("key", REFUSED_PRINCIPALS)
async def test_nobody_but_a_super_admin_session_uploads(key: str) -> None:
    with scoped_app() as (app, world):
        ids = with_system(world)
        r = await call(
            app,
            key,
            "POST",
            "/api/v1/ingest",
            files={"file": ("a.txt", b"hello there", "text/plain")},
            data={"corpus_id": str(ids.corpus)},
        )
        assert r.status_code in (403, 409), (key, r.text)
        assert r.status_code != 409 or r.json()["code"] == PROTECTED


async def test_a_super_admin_upload_gets_past_the_system_guard() -> None:
    with scoped_app() as (app, world):
        ids = with_system(world)
        r = await call(
            app,
            "sa",
            "POST",
            "/api/v1/ingest",
            files={"file": ("a.exe", b"MZ", "application/x-msdownload")},
            data={"corpus_id": str(ids.corpus)},
        )
        assert r.status_code == 400, r.text  # the file-type check, after the guard


@pytest.mark.parametrize("key", REFUSED_PRINCIPALS)
async def test_nobody_but_a_super_admin_session_deletes_a_document(key: str) -> None:
    with scoped_app() as (app, world):
        ids = with_system(world)
        doc = _doc(world, ids.corpus, external_id=None, meta={"cob_source": PLATFORM})
        r = await call(app, key, "DELETE", f"/api/v1/documents/{doc.id}?corpus_id={ids.corpus}")
        assert r.status_code in (403, 409), (key, r.text)
        assert r.status_code != 409 or r.json()["code"] == PROTECTED


async def test_a_super_admin_deletes_a_platform_doc_but_never_a_built_in_one() -> None:
    with scoped_app() as (app, world):
        ids = with_system(world)
        remove = AsyncMock()
        platform = _doc(world, ids.corpus, external_id="hours", meta={"cob_source": PLATFORM})
        with patch("callobuzz_rag.api.routes.documents.remove_document", remove):
            r = await call(
                app, "sa", "DELETE", f"/api/v1/documents/{platform.id}?corpus_id={ids.corpus}"
            )
            assert r.status_code == 204, r.text
            remove.assert_awaited_once()
            remove.reset_mock()
            built_in = _doc(world, ids.corpus, external_id=f"{RECORD_PREFIX}guide/flow#", meta=None)
            r = await call(
                app, "sa", "DELETE", f"/api/v1/documents/{built_in.id}?corpus_id={ids.corpus}"
            )
            assert r.status_code == 403 and r.json()["code"] == "cob_builtin_readonly", r.text
            remove.assert_not_awaited()


async def test_a_super_admin_cannot_use_other_write_routes_on_the_system_kb() -> None:
    with scoped_app() as (app, world):
        ids = with_system(world)
        for method, path, body in (
            ("PUT", f"/api/v1/corpora/{ids.corpus}", {"name": "x"}),
            ("DELETE", f"/api/v1/corpora/{ids.corpus}", None),
            ("POST", f"/api/v1/corpora/{ids.corpus}/rotate-key", None),
        ):
            r = await call(app, "sa", method, path, json=body)
            assert r.status_code == 409 and r.json()["code"] == PROTECTED, (path, r.text)


# ------------------------------------------------------------------ audience and the list


async def test_changing_the_audience_updates_the_row_and_the_vectors() -> None:
    from datetime import UTC, datetime

    with scoped_app() as (app, world):
        ids = with_system(world)
        doc = _doc(world, ids.corpus, external_id="hours", meta={"cob_source": PLATFORM})
        doc.ingested_at = datetime.now(UTC)
        world.rows[ids.corpus].__dict__.setdefault("qdrant_collection", "cob_docs")
        with patch(
            "callobuzz_rag.api.routes.cob_admin.get_system_corpus",
            AsyncMock(return_value=world.rows[ids.corpus]),
        ):
            world.session.get = AsyncMock(return_value=doc)
            r = await call(
                app,
                "sa",
                "PATCH",
                f"/api/v1/admin/cob/documents/{doc.id}",
                json={"audience": "admin"},
            )
        assert r.status_code == 200, r.text
        assert r.json()["audience"] == "admin" and r.json()["source"] == "platform"
        assert doc.doc_metadata["audience"] == "admin"
        app.state.vector_store.update_payload.assert_awaited_once()
        args = app.state.vector_store.update_payload.await_args.args
        assert args[1] == {"document_id": str(doc.id)} and args[2] == {"meta_audience": "admin"}


async def test_a_built_in_docs_audience_cannot_change_and_keys_are_refused() -> None:
    with scoped_app() as (app, world):
        ids = with_system(world)
        doc = _doc(world, ids.corpus, external_id=f"{RECORD_PREFIX}guide/flow#", meta=None)
        with patch(
            "callobuzz_rag.api.routes.cob_admin.get_system_corpus",
            AsyncMock(return_value=world.rows[ids.corpus]),
        ):
            world.session.get = AsyncMock(return_value=doc)
            url = f"/api/v1/admin/cob/documents/{doc.id}"
            r = await call(app, "sa", "PATCH", url, json={"audience": "public"})
            assert r.status_code == 403 and r.json()["code"] == "cob_builtin_readonly"
            r = await call(app, "sa", "PATCH", url, json={"audience": "everyone"})
            assert r.status_code == 422  # strict enum
            r = await call(app, "sa", "PATCH", url, json={"audience": "public", "x": 1})
            assert r.status_code == 422  # strict body
            for key in ("admin", "tk_a", "ck_a", "owner_a", "member_a"):
                r = await call(app, key, "PATCH", url, json={"audience": "public"})
                assert r.status_code in (401, 403), (key, r.text)
                r = await call(app, key, "GET", "/api/v1/admin/cob/documents")
                assert r.status_code in (401, 403), (key, r.text)


# ------------------------------------------------------------------ audience in retrieval


def test_an_admin_platform_doc_is_never_retrieved_for_a_customer() -> None:
    """The payload carries `meta_audience`; the filter COB sends is the role's list."""
    from callobuzz_rag.cob.types import CobRole, allowed_audiences
    from callobuzz_rag.vectordb.base import ChunkPayload

    user = SimpleNamespace(id=uuid.uuid4())

    def payload(audience: str | None) -> dict[str, Any]:
        supplied = {"audience": audience} if audience else None
        meta = platform_metadata(_request(user=user), supplied)
        return ChunkPayload(
            chunk_id="c", corpus_id="k", document_id="d", document_version_id="v", metadata=meta
        ).to_dict()

    def retrieved(role: CobRole, doc: dict[str, Any]) -> bool:
        return doc["meta_audience"] in allowed_audiences(role)

    secret, default, public = payload("admin"), payload(None), payload("public")
    assert secret["meta_audience"] == "admin" and secret["meta_cob_source"] == PLATFORM
    for role in (CobRole.OWNER, CobRole.MEMBER, CobRole.PUBLIC):
        assert not retrieved(role, secret)
    assert retrieved(CobRole.ADMIN, secret) and retrieved(CobRole.OPERATOR_OWNER, secret)
    assert retrieved(CobRole.OWNER, default) and not retrieved(CobRole.PUBLIC, default)
    assert retrieved(CobRole.PUBLIC, public)


# ------------------------------------------------------------------ the Docs group


async def test_the_docs_group_lists_only_platform_docs_the_role_may_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime

    from callobuzz_rag.cob import docs_service

    monkeypatch.setattr(
        "callobuzz_rag.cob.system.get_system_corpus",
        AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())),
    )

    def doc(name: str, meta: dict[str, str]) -> Any:
        return SimpleNamespace(
            id=uuid.uuid4(),
            file_name=name,
            external_id=None,
            doc_metadata=meta,
            ingested_at=datetime.now(UTC),
        )

    hours = doc("Support hours", {"cob_source": PLATFORM, "audience": "customer"})
    secret = doc("Margins", {"cob_source": PLATFORM, "audience": "admin"})
    builtin = doc("Guide", {"cob_source": BUILTIN, "audience": "customer"})
    session = _Session([hours, secret, builtin])

    topic = await docs_service.platform_topic(session, ["customer", "public"])
    assert topic is not None and topic.title == "From your platform"
    assert [p.title for p in topic.pages] == ["Support hours"]
    assert topic.pages[0].slug == f"platform/{hours.id}"
    as_admin = await docs_service.platform_topic(session, ["admin", "customer", "public"])
    assert as_admin is not None and {p.title for p in as_admin.pages} == {
        "Support hours",
        "Margins",
    }
    assert await docs_service.platform_topic(_Session([builtin]), ["customer"]) is None

    with pytest.raises(APIError) as caught:  # a hidden page is the same 404 as an unknown one
        await docs_service.platform_page(session, f"platform/{secret.id}", ["customer"], "v1")
    assert caught.value.status_code == 404 and caught.value.code == "docs_page_not_found"


# ------------------------------------------------------------------ the eval runner


def test_the_offline_eval_checks_the_platform_fixture_for_every_role() -> None:
    from callobuzz_rag.cob.eval import offline
    from callobuzz_rag.cob.kb import load_docs_index

    index = load_docs_index()
    assert offline.check_platform_leaks(index) == []
    report = offline.run_offline(index, [])
    assert report.platform_leaks == [] and report.summary()["platform_leaks"] == 0


def test_the_offline_eval_catches_a_platform_doc_that_leaks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from callobuzz_rag.cob.eval import offline
    from callobuzz_rag.cob.kb import load_docs_index

    leaky = tuple(
        (slug, "customer" if slug.endswith("admin") else audience, text)
        for slug, audience, text in offline.PLATFORM_FIXTURES
    )
    monkeypatch.setattr(offline, "PLATFORM_FIXTURES", leaky)
    problems = offline.check_platform_leaks(load_docs_index())
    assert {role for role, _ in problems} >= {"owner", "member"}


# ------------------------------------------------------------------ sources, search, the eval


def _raw(doc_id: uuid.UUID, audience: str | None, text: str = "Support is open 9 to 5.") -> Any:
    return {
        "index": 1,
        "chunk_id": str(uuid.uuid4()),
        "document_id": str(doc_id),
        "external_id": None,
        "file_name": "Support hours",
        "audience": audience,
        "text": text,
    }


def test_a_customer_platform_hit_is_a_source_for_an_owner_and_an_admin_one_is_dropped() -> None:
    from callobuzz_rag.cob.events import sources_from_director
    from callobuzz_rag.cob.kb import load_docs_index
    from callobuzz_rag.cob.types import CobRole

    index = load_docs_index()
    open_doc, closed_doc = uuid.uuid4(), uuid.uuid4()
    sources = [_raw(open_doc, "customer"), _raw(closed_doc, "admin")]
    got = sources_from_director(index, sources, role=CobRole.OWNER, context=None)
    assert [s.slug for s in got] == [f"platform/{open_doc}"]
    assert got[0].title == "Support hours" and got[0].anchor == "" and got[0].route is None
    as_admin = sources_from_director(index, sources, role=CobRole.ADMIN, context=None)
    assert len(as_admin) == 2


async def test_search_mode_and_docs_search_find_platform_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from callobuzz_rag.cob import docs_service, search_mode
    from callobuzz_rag.cob.kb import load_docs_index
    from callobuzz_rag.cob.types import CobRole

    index = load_docs_index()
    open_doc, closed_doc = uuid.uuid4(), uuid.uuid4()

    def chunk(doc_id: uuid.UUID, audience: str) -> Any:
        return SimpleNamespace(
            external_id=None,
            document_id=doc_id,
            file_name="Support hours" if audience == "customer" else "Margins",
            audience=audience,
            text="Support is open 9 to 5.",
            score=0.5,
        )

    chunks = [chunk(closed_doc, "admin"), chunk(open_doc, "customer")]
    result = SimpleNamespace(assembled=SimpleNamespace(cited_chunks=chunks))
    orchestrator = SimpleNamespace(run_retrieval_pipeline=AsyncMock(return_value=result))
    found = await search_mode.search(
        orchestrator, uuid.uuid4(), "support hours", index=index, role=CobRole.OWNER, context=None
    )
    assert [s.slug for s in found] == [f"platform/{open_doc}"]

    monkeypatch.setattr(
        "callobuzz_rag.cob.system.get_system_corpus",
        AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())),
    )
    monkeypatch.setattr(
        "callobuzz_rag.cob.orchestrator.build_retriever",
        lambda *_a, **_k: orchestrator,
    )
    out = await docs_service.search(
        MagicMock(), MagicMock(), "support hours", ["customer", "public"], index=index
    )
    platform = [i for i in out.items if i.slug.startswith("platform/")]
    assert [i.slug for i in platform] == [f"platform/{open_doc}"]
    assert platform[0].title == "Support hours"


async def test_the_synced_eval_flags_an_admin_platform_doc_returned_to_a_customer() -> None:
    from callobuzz_rag.cob.eval.items import EvalItem
    from callobuzz_rag.cob.eval.synced import SyncedHit, run_synced_eval
    from callobuzz_rag.cob.kb import load_docs_index

    item = EvalItem.model_validate(
        {
            "id": "plat-001",
            "role": "owner",
            "question": "What are the support hours?",
            "expect": {"behaviour": "answer", "slug": "screens/widget"},
        }
    )
    doc = uuid.uuid4()

    async def search(_q: str, audiences: list[str], _k: int) -> list[SyncedHit]:
        return [SyncedHit(f"platform/{doc}", "", "Margins are 40%", "admin")]

    summary = await run_synced_eval(search, [item], index=load_docs_index())
    assert summary["role_leaks"] == 1 and summary["passed"] is False

    async def fine(_q: str, audiences: list[str], _k: int) -> list[SyncedHit]:
        return [SyncedHit(f"platform/{doc}", "", "Support is 9 to 5", "customer")]

    assert (await run_synced_eval(fine, [item], index=load_docs_index()))["role_leaks"] == 0


# ------------------------------------------------------------------ uploads reach a search


async def test_a_super_admin_upload_is_a_new_confirmed_document() -> None:
    with scoped_app() as (app, world):
        ids = with_system(world)
        task = MagicMock()
        storage = MagicMock()
        storage.return_value.upload = AsyncMock()
        with (
            patch("callobuzz_rag.api.routes.ingest.object_storage", storage),
            patch("callobuzz_rag.api.routes.ingest.check_document_room", AsyncMock(return_value=0)),
            patch("callobuzz_rag.worker.tasks.ingest_document_task", task),
        ):
            r = await call(
                app,
                "sa",
                "POST",
                "/api/v1/ingest",
                files={"file": ("Guide.txt", b"hello there", "text/plain")},
                data={"corpus_id": str(ids.corpus)},
            )
        assert r.status_code == 202, r.text
        kwargs = task.delay.call_args.kwargs
        assert kwargs["auto_confirm"] is True and kwargs["mode"] == "new"
