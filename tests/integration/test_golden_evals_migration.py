"""phase12_005 on PostgreSQL: default sets for existing pairs, the expectation CHECK, cascades.

The data-step test is sync (alembic's env.py calls asyncio.run()). Requires Docker.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from testcontainers.postgres import PostgresContainer

from tests.integration.conftest import _async_url, new_tenant_id
from tests.integration.test_accounts_flow import _alembic
from tests.integration.test_api_keys_migration import _sql

pytestmark = pytest.mark.integration

PREVIOUS_HEAD = "phase12_002"


async def _corpus(db_session):
    from callobuzz_rag.db.models import Corpus

    corpus = Corpus(name=f"c7-{uuid.uuid4().hex[:6]}", tenant_id=await new_tenant_id(db_session))
    db_session.add(corpus)
    await db_session.flush()
    return corpus


async def test_behaviour_only_pair_and_cascade(db_session) -> None:
    from callobuzz_rag.db.models import GoldenPair, GoldenSet

    corpus = await _corpus(db_session)
    golden_set = GoldenSet(corpus_id=corpus.id, name="Patient help basics", is_default=True)
    db_session.add(golden_set)
    await db_session.flush()
    db_session.add(
        GoldenPair(
            corpus_id=corpus.id, set_id=golden_set.id, query="Hi", expected_behaviour="smalltalk"
        )
    )
    await db_session.flush()
    await db_session.execute(text("DELETE FROM corpora WHERE id = :id"), {"id": corpus.id})
    left = await db_session.execute(
        text("SELECT count(*) FROM golden_pair WHERE corpus_id = :id"), {"id": corpus.id}
    )
    assert left.scalar() == 0


async def test_a_pair_needs_an_expectation(db_session) -> None:
    from callobuzz_rag.db.models import GoldenPair, GoldenSet

    corpus = await _corpus(db_session)
    golden_set = GoldenSet(corpus_id=corpus.id, name="x")
    db_session.add(golden_set)
    await db_session.flush()
    db_session.add(
        GoldenPair(corpus_id=corpus.id, set_id=golden_set.id, query="Is home collection free?")
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_second_default_set_is_refused(db_session) -> None:
    from callobuzz_rag.db.models import GoldenSet

    corpus = await _corpus(db_session)
    db_session.add_all(
        [
            GoldenSet(corpus_id=corpus.id, name="a", is_default=True),
            GoldenSet(corpus_id=corpus.id, name="b", is_default=True),
        ]
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


def test_backfill_puts_existing_pairs_in_a_default_set() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, PREVIOUS_HEAD)
        tenant = _sql(
            url,
            "INSERT INTO tenants (name, api_key_hash, status, plan_id) "
            "SELECT 'Shree Labs', '', 'active', id FROM plans WHERE is_default RETURNING id",
        )[0][0]
        corpus = _sql(
            url,
            "INSERT INTO corpora (name, api_key_hash, qdrant_collection, tenant_id) "
            "VALUES ('Patient help', '', 'corpus_c7_001', :t) RETURNING id",
            t=tenant,
        )[0][0]
        for source in ("manual", "feedback"):
            _sql(
                url,
                "INSERT INTO golden_pair (corpus_id, query, expected_answer, source) "
                "VALUES (:c, :q, 'a', :s)",
                c=corpus,
                q=f"q-{source}",
                s=source,
            )
        _alembic(url, "phase12_005")
        sets = _sql(
            url, "SELECT id, is_default, name FROM golden_sets WHERE corpus_id = :c", c=corpus
        )
        assert len(sets) == 1 and sets[0][1] is True and sets[0][2] == "Golden questions"
        pairs = _sql(url, "SELECT set_id, origin, source FROM golden_pair ORDER BY source")
        assert [p[0] for p in pairs] == [sets[0][0], sets[0][0]]
        assert {p[2]: p[1] for p in pairs} == {"manual": "human", "feedback": "from_feedback"}
        _alembic(url, PREVIOUS_HEAD, down=True)
        assert _sql(url, "SELECT count(*) FROM golden_pair")[0][0] == 2
