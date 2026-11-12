"""TV2: reserve_ref against PostgreSQL (the partial unique index decides collisions)."""

import asyncio
import logging
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from callobuzz_rag.conversation.handoff import reference
from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.corpus import Corpus
from tests.integration.conftest import new_tenant_id

pytestmark = pytest.mark.integration


async def _kb_and_conversations(factory, n: int = 1):
    async with factory() as s:
        kb = Corpus(name=f"ref-{uuid.uuid4().hex[:6]}", tenant_id=await new_tenant_id(s))
        s.add(kb)
        await s.flush()
        convs = [Conversation(corpus_id=kb.id) for _ in range(n)]
        s.add_all(convs)
        await s.commit()
        return kb.id, [c.id for c in convs]


async def _ref_of(factory, cid):
    async with factory() as s:
        return (await s.get(Conversation, cid)).ref_code


async def test_assigns_once_and_is_stable(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    _, (cid,) = await _kb_and_conversations(factory)
    first = await reference.reserve_ref(factory, cid, "SD")
    assert first and first.startswith("SD-")
    assert await reference.reserve_ref(factory, cid, "SD") == first
    assert await _ref_of(factory, cid) == first


async def test_concurrent_reservations_agree(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    _, (cid,) = await _kb_and_conversations(factory)
    codes = await asyncio.gather(*(reference.reserve_ref(factory, cid, "SD") for _ in range(20)))
    assert len(set(codes)) == 1 and codes[0] is not None


async def test_collision_is_retried_and_other_kb_may_repeat(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    _, (a, b) = await _kb_and_conversations(factory, 2)
    assert await reference.reserve_ref(factory, a, "SD")
    taken = await _ref_of(factory, a)
    seq = iter([taken, taken, "SD-ZZZZ"])
    with patch.object(reference, "new_ref", lambda prefix, rng=None: next(seq)):
        assert await reference.reserve_ref(factory, b, "SD") == "SD-ZZZZ"
    # the same code in another knowledge base is allowed
    _, (other,) = await _kb_and_conversations(factory)
    with patch.object(reference, "new_ref", lambda prefix, rng=None: taken):
        assert await reference.reserve_ref(factory, other, "SD") == taken


async def test_eight_collisions_give_none(db_engine, caplog):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    _, (a, b) = await _kb_and_conversations(factory, 2)
    await reference.reserve_ref(factory, a, "SD")
    taken = await _ref_of(factory, a)
    with (
        patch.object(reference, "new_ref", lambda prefix, rng=None: taken),
        caplog.at_level(logging.WARNING),
    ):
        assert await reference.reserve_ref(factory, b, "SD") is None
    assert "No free reference code" in caplog.text
    assert await _ref_of(factory, b) is None


async def test_database_error_gives_none():
    def broken():
        raise RuntimeError("db down")

    assert await reference.reserve_ref(broken, uuid.uuid4(), "SD") is None


async def test_lookup_finds_matches_per_knowledge_base_newest_first(db_engine):
    from callobuzz_rag.db.models.handoff import Handoff

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    kb1, (c1,) = await _kb_and_conversations(factory)
    kb2, (c2,) = await _kb_and_conversations(factory)
    # a code of its own: the database is shared with the other tests, and the collision test
    # above leaves SD-ZZZZ on a conversation; find_matches(..., None) searches every one
    code = reference.new_ref("QX")
    async with factory() as s:
        for cid in (c1, c2):
            conv = await s.get(Conversation, cid)
            conv.ref_code = code
        corpus = await s.get(Corpus, kb1)
        s.add(
            Handoff(
                corpus_id=kb1,
                tenant_id=corpus.tenant_id,
                conversation_id=c1,
                channel="whatsapp",
                reason="explicit_request",
                status="new",
            )
        )
        await s.commit()
    async with factory() as s:
        both = await reference.find_matches(s, code, None)
        mine = await reference.find_matches(s, code, [kb1])
        none = await reference.find_matches(s, code, [uuid.uuid4()])
    assert {m["conversation_id"] for m in both} == {c1, c2}
    assert len(mine) == 1 and mine[0]["corpus_id"] == kb1 and len(mine[0]["handoff_ids"]) == 1
    assert none == []
    assert kb2 != kb1
