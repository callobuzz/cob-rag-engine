"""GET /api/v1/feedback: ratings with their question, scoped, filtered, paged (G-9)."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from tests.unit.scope_harness import MATRIX, call, check, scoped_app


def _result(*, rows=None, scalar=None, scalars=None) -> MagicMock:
    result = MagicMock()
    result.all.return_value = rows or []
    result.scalar.return_value = scalar
    result.scalars.return_value.all.return_value = scalars or []
    return result


@pytest.mark.parametrize(("key", "which", "outcome"), MATRIX)
async def test_scoping(key, which, outcome) -> None:
    with scoped_app() as (app, world):
        res = await call(app, key, "GET", f"/api/v1/feedback?corpus_id={world.corpus(which).id}")
    check(outcome, res.status_code, res.text)


async def test_items_carry_question_query_and_corpus() -> None:
    fb = SimpleNamespace(
        id=uuid.uuid4(),
        query_log_id=uuid.uuid4(),
        rating=2,
        correction="Open on Sundays too",
        feedback_type="explicit",
        session_id="s1",
        created_at=datetime(2026, 9, 30, tzinfo=UTC),
    )
    with scoped_app() as (app, world):
        world.session.execute = AsyncMock(
            side_effect=[
                _result(scalar=1),
                _result(rows=[(fb, "When do you open?", world.corpus_a.id)]),
            ]
        )
        res = await call(
            app,
            "ck_a",
            "GET",
            "/api/v1/feedback?min_rating=1&max_rating=2&start=2026-09-01T00:00:00Z&limit=5",
        )
        page = world.session.execute.await_args_list[1].args[0]
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["total"], body["offset"], body["limit"]) == (1, 0, 5)
    assert body["items"] == [
        {
            "id": str(fb.id),
            "query_id": str(fb.query_log_id),
            "corpus_id": str(world.corpus_a.id),
            "question": "When do you open?",
            "rating": 2,
            "correction": "Open on Sundays too",
            "feedback_type": "explicit",
            "session_id": "s1",
            "created_at": "2026-09-30T00:00:00Z",
        }
    ]
    sql = str(page.compile(dialect=postgresql.dialect()))
    assert "query_log.corpus_id IN" in sql
    assert "feedback.rating >=" in sql and "feedback.rating <=" in sql
    assert "feedback.created_at >=" in sql


async def test_tenant_without_corpus_id_sees_its_corpora() -> None:
    with scoped_app() as (app, world):
        world.session.execute = AsyncMock(
            side_effect=[_result(scalars=[world.corpus_a.id]), _result(scalar=0), _result()]
        )
        res = await call(app, "tk_a", "GET", "/api/v1/feedback")
        page = world.session.execute.await_args_list[2].args[0]
    assert res.status_code == 200
    params = page.compile(dialect=postgresql.dialect()).params
    assert [world.corpus_a.id] in params.values()


@pytest.mark.parametrize(
    "query", ["min_rating=4&max_rating=2", "min_rating=0", "max_rating=6", "limit=101"]
)
async def test_bad_filters_are_422(query) -> None:
    with scoped_app() as (app, _):
        res = await call(app, "admin", "GET", f"/api/v1/feedback?{query}")
    assert res.status_code == 422
