"""phase12_001 on PostgreSQL: the table, its one-draft / one-published invariants, and the
data step that switches the assistant on for every existing knowledge base (SV 2026-10-02).

Sync tests (alembic's env.py calls asyncio.run()). Requires Docker.
"""

import json
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from testcontainers.postgres import PostgresContainer

from tests.integration.conftest import _async_url
from tests.integration.test_accounts_flow import _alembic
from tests.integration.test_api_keys_migration import _sql

pytestmark = pytest.mark.integration

PREVIOUS_HEAD = "phase11_014"


def _tenant(url: str) -> Any:
    return _sql(
        url,
        "INSERT INTO tenants (name, api_key_hash, status, plan_id) "
        "SELECT 'Shree Labs', '', 'active', id FROM plans WHERE is_default RETURNING id",
    )[0][0]


def _corpus(
    url: str, tenant: Any, name: str, collection: str, prompt: dict[str, Any] | None
) -> str:
    rows = _sql(
        url,
        "INSERT INTO corpora (name, api_key_hash, qdrant_collection, tenant_id, prompt_config) "
        "VALUES (:n, '', :q, :t, CAST(:p AS jsonb)) RETURNING id",
        n=name,
        q=collection,
        t=tenant,
        p=json.dumps(prompt) if prompt is not None else None,
    )
    return str(rows[0][0])


def _config(value: Any) -> dict[str, Any]:
    return json.loads(value) if isinstance(value, str) else dict(value)


def test_existing_corpora_get_a_published_v1_and_invariants_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cryptography.fernet import Fernet

    monkeypatch.setenv("RAG_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, PREVIOUS_HEAD)
        tenant = _tenant(url)
        plain = _corpus(url, tenant, "Patient help", "corpus_plain001", None)
        legacy = _corpus(
            url,
            tenant,
            "Staff SOPs",
            "corpus_legacy01",
            {"system_prompt": "Answer as the Shree Labs front desk.", "constraints": "Be brief."},
        )
        long_prompt = "x" * 10_000
        long = _corpus(
            url, tenant, "Long prompt", "corpus_long0001", {"system_prompt": long_prompt}
        )
        hindi = _corpus(url, tenant, "Hindi desk", "corpus_hindi001", {"response_language": "hi"})
        odd = _corpus(
            url, tenant, "Odd language", "corpus_odd00001", {"response_language": "Spanish"}
        )

        _alembic(url, "phase12_001")
        rows = _sql(
            url,
            "SELECT corpus_id::text, version, status, config FROM assistant_configs "
            "ORDER BY created_at",
        )
        by_corpus = {r[0]: _config(r[3]) for r in rows}
        assert set(by_corpus) == {plain, legacy, long, hindi, odd}
        for _cid, version, status, _value in rows:
            assert (version, status) == (1, "published")
        for config in by_corpus.values():
            assert config["enabled"] is True
            assert config["identity"]["business_type"] == "generic_support"
            assert config["leads"]["enabled"] is True  # an encryption key is configured
        assert by_corpus[plain]["advanced"].get("extra_instructions") in (None, "")
        assert by_corpus[legacy]["advanced"]["extra_instructions"] == (
            "Answer as the Shree Labs front desk.\n\nBe brief."
        )
        assert by_corpus[long]["advanced"]["extra_instructions"] == long_prompt
        assert by_corpus[hindi]["language"]["primary"] == "hi"
        assert by_corpus[hindi]["language"]["reply_policy"] == "fixed"
        assert by_corpus[odd]["advanced"]["extra_instructions"] == "Always reply in Spanish."

        with pytest.raises(IntegrityError, match="uq_assistant_configs_published"):
            _sql(
                url,
                "INSERT INTO assistant_configs (corpus_id, version, status, config, "
                "schema_version, created_by) VALUES (:c, 2, 'published', '{}', 1, 'test')",
                c=plain,
            )
        _sql(
            url,
            "INSERT INTO assistant_configs (corpus_id, status, config, schema_version, "
            "created_by) VALUES (:c, 'draft', '{}', 1, 'test')",
            c=plain,
        )
        with pytest.raises(IntegrityError, match="uq_assistant_configs_draft"):
            _sql(
                url,
                "INSERT INTO assistant_configs (corpus_id, status, config, schema_version, "
                "created_by) VALUES (:c, 'draft', '{}', 1, 'test')",
                c=plain,
            )
        with pytest.raises(IntegrityError, match="ck_assistant_configs_status"):
            _sql(
                url,
                "INSERT INTO assistant_configs (corpus_id, version, status, config, "
                "schema_version, created_by) VALUES (:c, 9, 'live', '{}', 1, 'test')",
                c=legacy,
            )
        with pytest.raises(IntegrityError, match="ck_assistant_configs_draft_version"):
            _sql(
                url,
                "INSERT INTO assistant_configs (corpus_id, version, status, config, "
                "schema_version, created_by) VALUES (:c, 7, 'draft', '{}', 1, 'test')",
                c=legacy,
            )

        _sql(url, "DELETE FROM corpora WHERE id = :c", c=plain)  # CASCADE
        count = _sql(url, "SELECT count(*) FROM assistant_configs WHERE corpus_id = :c", c=plain)
        assert count == [(0,)]

        _alembic(url, PREVIOUS_HEAD, down=True)
        assert _sql(url, "SELECT to_regclass('assistant_configs')") == [(None,)]
        _alembic(url, "head")


def test_without_an_encryption_key_leads_start_switched_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leads need RAG_ENCRYPTION_KEY (publish check); without one the migrated v1 has
    leads.enabled=false, exactly like a knowledge base created without a key."""
    monkeypatch.setenv("RAG_ENCRYPTION_KEY", "")
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, PREVIOUS_HEAD)
        _corpus(url, _tenant(url), "Admissions help", "corpus_admit001", None)
        _alembic(url, "phase12_001")
        (config,) = _sql(url, "SELECT config FROM assistant_configs")[0]
        config = _config(config)
        assert config["enabled"] is True
        assert config["leads"]["enabled"] is False


async def test_store_draft_publish_restore_enable(db_session) -> None:  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    from callobuzz_rag.api.errors import APIError
    from callobuzz_rag.conversation.schema import AssistantConfig
    from callobuzz_rag.conversation.store import AssistantConfigStore, get_runtime_config
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.db.models.corpus import Corpus
    from tests.integration.conftest import new_tenant_id

    tenant_id = await new_tenant_id(db_session)
    corpus = Corpus(
        name="Patient help",
        tenant_id=tenant_id,
        qdrant_collection="corpus_store01",
        api_key_hash="",
    )
    db_session.add(corpus)
    await db_session.flush()
    settings = Settings(_env_file=None, encryption_key="")
    request = SimpleNamespace(
        state=SimpleNamespace(is_admin=True),
        app=SimpleNamespace(state=SimpleNamespace(settings=settings)),
        client=None,
        headers={},
    )
    store = AssistantConfigStore(db_session, request, settings)  # type: ignore[arg-type]

    first = await store.publish_initial(
        corpus, AssistantConfig.model_validate({"leads": {"enabled": False}}), "created"
    )
    assert first.version == 1
    draft = await store.patch_draft(corpus, {"voice": {"emoji": "light"}}, base_revision=None)
    assert draft.version is None and draft.draft_revision == 1
    with pytest.raises(APIError) as conflict:
        await store.patch_draft(corpus, {"voice": {"emoji": "none"}}, base_revision=0)
    assert conflict.value.code == "assistant_draft_conflict"
    assert conflict.value.extra == {"current_revision": 1}
    v2 = await store.publish(corpus, note="emoji", base_revision=1)
    assert v2.version == 2
    published, draft_after = await store.state(corpus.id)
    assert published is not None and published.version == 2 and draft_after is None
    v3 = await store.restore(corpus, 1)
    assert (v3.version, v3.restored_from, v3.config["voice"]["emoji"]) == (3, 1, "none")
    v4 = await store.set_enabled(corpus, False)
    assert v4.version == 4 and v4.config["enabled"] is False
    runtime = await get_runtime_config(db_session, corpus.id)
    assert runtime is not None and runtime.enabled is False and runtime.version == 4
    assert runtime.draft_revision is None
    preview_draft = await store.patch_draft(corpus, {"voice": {"emoji": "light"}}, base_revision=0)
    preview = await get_runtime_config(db_session, corpus.id, use_draft=True)
    assert preview is not None and preview.version is None
    assert preview.draft_revision == preview_draft.draft_revision == 1
    assert preview.config.voice.emoji == "light"
    await store.discard_draft(corpus)
    rows, total = await store.list_versions(corpus.id, 0, 10)
    assert total == 4
    assert [r.status for r in rows] == ["published", "archived", "archived", "archived"]
    assert [r.version for r in rows] == [4, 3, 2, 1]
    page, _ = await store.list_versions(corpus.id, 1, 2)
    assert [r.version for r in page] == [3, 2]


async def test_concurrent_writers_are_serialised(db_engine) -> None:  # type: ignore[no-untyped-def]
    """Two writers on one knowledge base from separate sessions: one wins, the other gets the
    typed 409 or a no-op, never an IntegrityError or a StaleDataError."""
    import asyncio
    from types import SimpleNamespace

    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from callobuzz_rag.api.errors import APIError
    from callobuzz_rag.conversation.schema import AssistantConfig
    from callobuzz_rag.conversation.store import AssistantConfigStore
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.db.models.assistant_config import AssistantConfigVersion
    from callobuzz_rag.db.models.corpus import Corpus
    from tests.integration.conftest import new_tenant_id

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    settings = Settings(_env_file=None, encryption_key="")
    request = SimpleNamespace(
        state=SimpleNamespace(is_admin=True),
        app=SimpleNamespace(state=SimpleNamespace(settings=settings)),
        client=None,
        headers={},
    )
    async with factory() as s:
        corpus = Corpus(
            name="Race",
            tenant_id=await new_tenant_id(s),
            qdrant_collection="corpus_race001",
            api_key_hash="",
        )
        s.add(corpus)
        await s.flush()
        corpus_id = corpus.id
        store = AssistantConfigStore(s, request, settings)  # type: ignore[arg-type]
        await store.publish_initial(
            corpus, AssistantConfig.model_validate({"leads": {"enabled": False}}), "created"
        )
        await s.commit()

    async def run(action: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        async with factory() as session:
            corpus = await session.get(Corpus, corpus_id)
            store = AssistantConfigStore(session, request, settings)  # type: ignore[arg-type]
            try:
                await getattr(store, action)(corpus, *args, **kwargs)
                await session.commit()
                return "ok"
            except APIError as exc:
                await session.rollback()
                return exc.code

    # two first drafts with the same base revision
    first = await asyncio.gather(
        run("patch_draft", {"voice": {"emoji": "light"}}, 0),
        run("patch_draft", {"voice": {"emoji": "none"}}, 0),
    )
    assert sorted(first) == ["assistant_draft_conflict", "ok"]

    # two publishes of the same draft
    both = await asyncio.gather(
        run("publish", note=None, base_revision=1), run("publish", note=None, base_revision=1)
    )
    assert sorted(both) == ["assistant_draft_conflict", "ok"]

    # a double click on "switch off"
    off = await asyncio.gather(run("set_enabled", False), run("set_enabled", False))
    assert off == ["ok", "ok"]

    async with factory() as s:
        rows = (
            (
                await s.execute(
                    select(AssistantConfigVersion).where(
                        AssistantConfigVersion.corpus_id == corpus_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert sorted(r.version for r in rows if r.version) == [1, 2, 3]
        assert [r.status for r in rows].count("published") == 1
        assert (
            await s.execute(
                select(func.count()).where(
                    AssistantConfigVersion.corpus_id == corpus_id,
                    AssistantConfigVersion.status == "draft",
                )
            )
        ).scalar() == 0
