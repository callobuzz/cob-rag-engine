"""COB's questions are metered as source `cob` on the asking workspace, and the workspace's
usage report includes them (and only them) from COB's own knowledge base."""

import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.dialects import postgresql

from callobuzz_rag.analytics.usage import (
    _UPSERT_CONSTRAINT,
    SOURCE_COB,
    SOURCE_COB_PUBLIC,
    usage_report,
)
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.usage import UsageDaily
from tests.unit.scope_harness import call


def _sql(stmt) -> str:  # type: ignore[no-untyped-def]
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_sources_and_the_upsert_key() -> None:
    assert SOURCE_COB == "cob" and SOURCE_COB_PUBLIC == "cob_public"
    assert _UPSERT_CONSTRAINT == "uq_usage_daily_day_corpus_tenant_source_payer_model"
    constraint = next(c for c in UsageDaily.__table__.constraints if c.name == _UPSERT_CONSTRAINT)
    assert [c.name for c in constraint.columns] == [  # type: ignore[attr-defined]
        "day", "corpus_id", "tenant_id", "source", "payer", "model"
    ]  # fmt: skip


async def test_the_director_turn_is_metered_on_the_asking_workspace(cob) -> None:  # type: ignore[no-untyped-def]
    await cob.sse("alice", {"message": "How do I embed the widget?"})
    turn = cob.director.turn
    assert (turn.usage_source, turn.tenant_id, turn.corpus_id) == (
        "cob",
        cob.world.shree.id,
        cob.world.corpus.id,
    )
    await cob.sse("alice_anand", {"message": "How do I embed the widget?"})
    assert cob.director.turn.tenant_id == cob.world.anand.id


async def test_a_turn_that_called_a_model_counts_and_one_that_did_not_is_free(cob) -> None:  # type: ignore[no-untyped-def]
    events = await cob.sse("alice", {"message": "How do I embed the widget?"})
    assert events[-1].data["counted"] is True
    cob.director.model_calls = 0
    events = await cob.sse("alice", {"message": "How do I embed the widget?"})
    assert events[-1].data["counted"] is False


async def test_search_mode_and_the_administrator_reply_are_free(cob) -> None:  # type: ignore[no-untyped-def]
    cob.world.quota = (1, 1)
    events = await cob.sse("alice", {"message": "How do I rotate a key?"})
    assert events[-1].data["counted"] is False
    cob.world.quota = None
    cob.retriever.records = [("screens/engine", 0.8), ("screens/widget", 0.4)]
    events = await cob.sse("alice", {"message": "Where is the .env file?"})
    assert events[-1].data["counted"] is False


async def test_report_adds_only_this_workspaces_cob_rows() -> None:
    result = MagicMock()
    result.all.return_value = []
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    system, tenant = uuid.uuid4(), uuid.uuid4()
    await usage_report(
        session,
        corpus_ids=[],  # a workspace that owns no knowledge base yet still reads its COB rows
        start=date(2026, 9, 1),
        end=date(2026, 9, 30),
        group_by="source",
        cob_scope=(system, tenant),
    )
    sql = _sql(session.execute.await_args.args[0])
    assert "usage_daily.corpus_id IN" in sql and " OR " in sql
    assert sql.count("usage_daily.tenant_id =") == 1
    params = session.execute.await_args.args[0].compile(dialect=postgresql.dialect()).params
    assert system in params.values() and tenant in params.values()


async def test_report_without_cob_scope_is_unchanged() -> None:
    session = AsyncMock()
    assert (
        await usage_report(
            session, corpus_ids=[], start=date.today(), end=date.today(), group_by="day"
        )
        == []
    )
    session.execute.assert_not_awaited()


async def test_the_usage_route_gives_a_workspace_its_cob_scope(cob) -> None:  # type: ignore[no-untyped-def]
    seen: list[dict] = []  # type: ignore[type-arg]

    async def spy(session, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(kwargs)
        return []

    async def scope_of(key: str, query: str = "group_by=source") -> object:
        seen.clear()
        res = await call(cob.app, key, "GET", f"/api/v1/usage?{query}")
        assert res.status_code == 200, (key, res.text)
        assert {s["cob_scope"] for s in seen} == {seen[0]["cob_scope"]}  # both report calls agree
        return seen[0]["cob_scope"]

    with patch("callobuzz_rag.api.routes.usage.usage_report", spy):
        system, shree = cob.world.corpus.id, cob.world.shree.id
        assert await scope_of("alice") == (system, shree)
        assert await scope_of("tk_a") == (system, shree)
        assert await scope_of("sa") is None  # an admin already reads every row
        assert await scope_of("admin") is None
        assert await scope_of("ck_a") is None  # a knowledge-base key reads that knowledge base only
        own_kb = cob.db.add(
            Corpus(
                id=cob.world.corpus_a.id,
                name="Shree KB",
                qdrant_collection="kb_a",
                tenant_id=shree,
                is_system=False,
            )
        )
        assert await scope_of("alice", f"corpus_id={own_kb.id}") is None
        # An operator looking at one workspace reads the COB questions that workspace asked.
        assert await scope_of("sa", f"group_by=source&tenant_id={shree}") == (system, shree)
        assert await scope_of("admin", f"group_by=source&tenant_id={shree}") == (system, shree)
        assert await scope_of("sa", f"group_by=source&corpus_id={own_kb.id}") is None


async def test_a_follow_up_call_alone_does_not_make_a_turn_a_question(cob) -> None:  # type: ignore[no-untyped-def]
    # A verbatim answer made no answer-model call; the AI follow-up after it did, and the
    # director metered requests=0, so `counted` must say the same.
    cob.director.model_calls = 1
    cob.director.answer_calls = 0
    events = await cob.sse("alice", {"message": "How do I embed the widget?"})
    assert events[-1].data["counted"] is False
    cob.director.answer_calls = 1
    events = await cob.sse("alice", {"message": "How do I embed the widget?"})
    assert events[-1].data["counted"] is True
