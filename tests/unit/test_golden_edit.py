"""Golden pairs are editable; the list reports its total (G-20)."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.db.models.golden_pair import GoldenPair
from tests.unit.scope_harness import MATRIX, call, check, scoped_app


def _pair(corpus_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        corpus_id=corpus_id,
        query="When do you open?",
        expected_answer="9am",
        expected_chunk_ids=[uuid.uuid4()],
        pair_metadata={"tag": "hours"},
        source="manual",
        created_at=datetime(2026, 9, 30, tzinfo=UTC),
        set_id=uuid.uuid4(),
        expected_behaviour=None,
        expected_label=None,
        must_include=[],
        must_cite=None,
        origin="human",
        difficulty=None,
        topic=None,
    )


@pytest.mark.parametrize(("key", "which", "outcome"), MATRIX)
async def test_patch_scoping(key, which, outcome) -> None:
    with scoped_app() as (app, world):
        pair = _pair(world.corpus(which).id)
        world.add_row(GoldenPair, pair)
        res = await call(
            app, key, "PATCH", f"/api/v1/golden/{pair.id}", json={"expected_answer": "10am"}
        )
    check(outcome, res.status_code, res.text)
    assert (pair.expected_answer == "10am") is (outcome == "allowed")


async def test_patch_changes_only_what_is_sent_and_null_clears() -> None:
    with scoped_app() as (app, world):
        pair = _pair(world.corpus_a.id)
        world.add_row(GoldenPair, pair)
        res = await call(
            app,
            "ck_a",
            "PATCH",
            f"/api/v1/golden/{pair.id}",
            json={"query": "Opening hours?", "metadata": None},
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["query"] == "Opening hours?" and body["expected_answer"] == "9am"
    assert body["metadata"] is None and pair.pair_metadata is None
    assert len(body["expected_chunk_ids"]) == 1


@pytest.mark.parametrize(
    "body",
    [{"query": None}, {"expected_answer": ""}, {"query": "x" * 5001}, {"corpus_id": "c"}],
)
async def test_patch_validation(body) -> None:
    with scoped_app() as (app, world):
        pair = _pair(world.corpus_a.id)
        world.add_row(GoldenPair, pair)
        res = await call(app, "ck_a", "PATCH", f"/api/v1/golden/{pair.id}", json=body)
    assert res.status_code == 422


async def test_unknown_pair_is_404() -> None:
    with scoped_app() as (app, _):
        res = await call(app, "admin", "PATCH", f"/api/v1/golden/{uuid.uuid4()}", json={})
    assert res.status_code == 404


async def test_list_reports_total_and_paging() -> None:
    with scoped_app() as (app, world):
        page = MagicMock()
        page.scalars.return_value.all.return_value = [_pair(world.corpus_a.id)]
        count = MagicMock()
        count.scalar.return_value = 57
        world.session.execute = AsyncMock(side_effect=[page, count])
        res = await call(
            app, "ck_a", "GET", f"/api/v1/golden?corpus_id={world.corpus_a.id}&offset=50&limit=1"
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["count"], body["total"], body["offset"], body["limit"]) == (1, 57, 50, 1)
