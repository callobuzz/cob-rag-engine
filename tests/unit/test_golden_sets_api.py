"""Golden sets: CRUD, pair limit, template import, behaviour-only pairs, criteria, judge settings."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from callobuzz_rag.db.models import EvalCriterion, EvalSettings, GoldenPair, GoldenSet
from tests.unit.scope_harness import call, scoped_app

pytestmark = pytest.mark.usefixtures("unlimited_plan")

KEY = "owner_a"
PAIR = {
    "query": "Is home collection free?",
    "expected_answer": "Free above 999 rupees; 150 within Pune otherwise.",
    "topic": "home collection",
}
FERNET = "9cN0RuH9mx0wQvU4X1x6m1jwKkq3m4b1dgM2b0bq0Zg="


class _Result:
    """A query result with the rows a test gives it, whatever shape the route asks for."""

    def __init__(self, first: Any = None, rows: list[Any] | None = None, count: int = 0) -> None:
        self._first = first
        self._rows = rows if rows is not None else ([] if first is None else [first])
        self._count = count

    def scalars(self) -> "_Result":
        return self

    def all(self) -> list[Any]:
        return self._rows

    def scalar_one_or_none(self) -> Any:
        return self._first

    def scalar(self) -> int:
        return self._count


def _use(world: Any, result: _Result) -> None:
    world.session.execute = AsyncMock(return_value=result)

    async def refresh(obj: Any, *a: Any, **k: Any) -> None:
        for name, value in (
            ("id", uuid.uuid4()),
            ("created_at", datetime.now(UTC)),
            ("updated_at", datetime.now(UTC)),
        ):
            if hasattr(obj, name) and getattr(obj, name, None) is None:
                setattr(obj, name, value)

    world.session.refresh = AsyncMock(side_effect=refresh)


def _configure(app: Any, **overrides: Any) -> None:
    app.state.settings = app.state.settings.model_copy(update=overrides)


def _added(world: Any, model: type) -> list[Any]:
    return [c.args[0] for c in world.session.add.call_args_list if isinstance(c.args[0], model)]


async def test_create_set_with_answer_and_behaviour_pairs() -> None:
    with scoped_app() as (app, world):
        _use(world, _Result())
        res = await call(
            app,
            KEY,
            "POST",
            f"/api/v1/corpora/{world.corpus_a.id}/golden-sets",
            json={
                "name": "Basics",
                "pairs": [PAIR, {"query": "Hi", "expected_behaviour": "smalltalk"}],
            },
        )
        assert res.status_code == 201, res.text
        pairs = _added(world, GoldenPair)
    assert [p.expected_behaviour for p in pairs] == [None, "smalltalk"]
    assert pairs[0].topic == "home collection" and pairs[0].origin == "human"
    assert res.json()["pair_count"] == 2 and res.json()["behaviour_count"] == 1


async def test_pair_without_any_expectation_is_422() -> None:
    with scoped_app() as (app, world):
        res = await call(
            app,
            KEY,
            "POST",
            f"/api/v1/corpora/{world.corpus_a.id}/golden-sets",
            json={"name": "x", "pairs": [{"query": "Hi"}]},
        )
    assert res.status_code == 422


async def test_unknown_field_and_bad_language_are_422() -> None:
    with scoped_app() as (app, world):
        url = f"/api/v1/corpora/{world.corpus_a.id}/golden-sets"
        extra = await call(app, KEY, "POST", url, json={"name": "x", "colour": "red"})
        lang = await call(
            app,
            KEY,
            "POST",
            url,
            json={"name": "x", "pairs": [{**PAIR, "language": "klingon"}]},
        )
    assert extra.status_code == 422 and lang.status_code == 422


async def test_pair_limit() -> None:
    with scoped_app() as (app, world):
        _configure(app, golden_set_max_pairs=3)
        res = await call(
            app,
            KEY,
            "POST",
            f"/api/v1/corpora/{world.corpus_a.id}/golden-sets",
            json={"name": "Big", "pairs": [PAIR] * 4},
        )
    assert res.status_code == 422
    assert res.json() == {
        "detail": "A golden set holds at most 3 questions",
        "code": "golden_set_limit",
        "limit": 3,
    }


async def test_adding_pairs_counts_the_existing_ones() -> None:
    with scoped_app() as (app, world):
        _configure(app, golden_set_max_pairs=3)
        existing = GoldenSet(id=uuid.uuid4(), corpus_id=world.corpus_a.id, name="x")
        # first query: the set, second: the count of its pairs
        results = [_Result(existing), _Result(count=2)]
        world.session.execute = AsyncMock(side_effect=results)
        res = await call(
            app,
            KEY,
            "POST",
            f"/api/v1/corpora/{world.corpus_a.id}/golden-sets/{existing.id}/pairs",
            json={"pairs": [PAIR, PAIR]},
        )
    assert res.status_code == 422 and res.json()["code"] == "golden_set_limit"


async def test_unknown_set_is_404() -> None:
    with scoped_app() as (app, world):
        _use(world, _Result(None))
        res = await call(
            app,
            KEY,
            "POST",
            f"/api/v1/corpora/{world.corpus_a.id}/golden-sets/{uuid.uuid4()}/pairs",
            json={"pairs": [PAIR]},
        )
    assert res.status_code == 404 and res.json()["code"] == "golden_set_not_found"


async def test_from_template_makes_behaviour_pairs() -> None:
    with scoped_app() as (app, world):
        _use(world, _Result())
        res = await call(
            app,
            KEY,
            "POST",
            f"/api/v1/corpora/{world.corpus_a.id}/golden-sets/from-template",
            json={"template": "clinic"},
        )
        pairs = _added(world, GoldenPair)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["source"] == "template" and body["behaviour_count"] == body["pair_count"] > 0
    assert all(p.expected_behaviour and p.expected_answer is None for p in pairs)


async def test_unknown_template_is_422() -> None:
    with scoped_app() as (app, world):
        res = await call(
            app,
            KEY,
            "POST",
            f"/api/v1/corpora/{world.corpus_a.id}/golden-sets/from-template",
            json={"template": "spaceship"},
        )
    assert res.status_code == 422


async def test_the_default_set_cannot_be_deleted() -> None:
    with scoped_app() as (app, world):
        default = GoldenSet(
            id=uuid.uuid4(), corpus_id=world.corpus_a.id, name="Golden questions", is_default=True
        )
        _use(world, _Result(default))
        res = await call(
            app, KEY, "DELETE", f"/api/v1/corpora/{world.corpus_a.id}/golden-sets/{default.id}"
        )
    assert res.status_code == 409 and res.json()["code"] == "golden_set_is_default"


async def test_judge_settings_encrypt_the_key_and_hide_it() -> None:
    with scoped_app() as (app, world):
        _configure(app, encryption_key=FERNET)
        res = await call(
            app,
            KEY,
            "PUT",
            f"/api/v1/corpora/{world.corpus_a.id}/eval/settings",
            json={
                "judge_provider": "openrouter",
                "judge_model": "judge-x",
                "judge_api_key": "sk-judge",
                "thresholds": {"answer_correctness": 0.8},
            },
        )
        assert res.status_code == 200, res.text
        stored = _added(world, EvalSettings)[0]
    assert stored.judge_api_key_encrypted and "sk-judge" not in stored.judge_api_key_encrypted
    body = res.json()
    assert body["judge_has_key"] is True and "judge_api_key" not in body
    assert body["effective_thresholds"]["answer_correctness"] == 0.8
    assert body["effective_thresholds"]["faithfulness"] == 0.9


async def test_judge_key_needs_the_encryption_key() -> None:
    with scoped_app() as (app, world):
        _configure(app, encryption_key="")
        res = await call(
            app,
            KEY,
            "PUT",
            f"/api/v1/corpora/{world.corpus_a.id}/eval/settings",
            json={"judge_api_key": "sk-judge"},
        )
    assert res.status_code == 503 and res.json()["code"] == "encryption_not_configured"


async def test_unknown_threshold_name_is_422() -> None:
    with scoped_app() as (app, world):
        res = await call(
            app,
            KEY,
            "PUT",
            f"/api/v1/corpora/{world.corpus_a.id}/eval/settings",
            json={"thresholds": {"vibes": 0.5}},
        )
    assert res.status_code == 422 and res.json()["code"] == "eval_thresholds_invalid"


async def test_a_member_cannot_change_the_judge() -> None:
    with scoped_app() as (app, world):
        res = await call(
            app,
            "member_a",
            "PUT",
            f"/api/v1/corpora/{world.corpus_a.id}/eval/settings",
            json={"judge_model": "judge-x"},
        )
    assert res.status_code == 403 and res.json()["code"] == "owner_required"


async def test_duplicate_criterion_name_is_409() -> None:
    with scoped_app() as (app, world):
        _use(world, _Result(first=uuid.uuid4()))
        res = await call(
            app,
            KEY,
            "POST",
            f"/api/v1/corpora/{world.corpus_a.id}/eval/criteria",
            json={"name": "mentions-fee", "definition": "States the fee"},
        )
    assert res.status_code == 409 and res.json()["code"] == "eval_criterion_exists"


async def test_criterion_names_are_slugs() -> None:
    with scoped_app() as (app, world):
        res = await call(
            app,
            KEY,
            "POST",
            f"/api/v1/corpora/{world.corpus_a.id}/eval/criteria",
            json={"name": "Mentions Fee!", "definition": "x"},
        )
    assert res.status_code == 422


async def test_create_and_update_a_criterion() -> None:
    with scoped_app() as (app, world):
        _use(world, _Result(None))
        base = f"/api/v1/corpora/{world.corpus_a.id}/eval/criteria"
        created = await call(
            app, KEY, "POST", base, json={"name": "mentions-fee", "definition": "States the fee"}
        )
        assert created.status_code == 201, created.text
        row = _added(world, EvalCriterion)[0]
        row.id = uuid.UUID(created.json()["id"]) if created.json().get("id") else uuid.uuid4()
        row.threshold = 0.7
        row.enabled = True
        _use(world, _Result(row))
        patched = await call(
            app, KEY, "PATCH", f"{base}/{row.id}", json={"threshold": 0.9, "enabled": False}
        )
    assert patched.status_code == 200, patched.text
    assert patched.json()["threshold"] == 0.9 and patched.json()["enabled"] is False


async def test_pair_create_accepts_behaviour_only_and_checks_the_set() -> None:
    with scoped_app() as (app, world):
        _use(world, _Result(SimpleNamespace(id=uuid.uuid4(), is_default=True)))
        world.session.commit = AsyncMock()
        res = await call(
            app,
            KEY,
            "POST",
            "/api/v1/golden",
            json={
                "corpus_id": str(world.corpus_a.id),
                "query": "Hi",
                "expected_behaviour": "smalltalk",
            },
        )
        assert res.status_code == 201, res.text
        _use(world, _Result(None))
        unknown_set = await call(
            app,
            KEY,
            "POST",
            "/api/v1/golden",
            json={
                "corpus_id": str(world.corpus_a.id),
                "query": "Hi",
                "expected_behaviour": "smalltalk",
                "set_id": str(uuid.uuid4()),
            },
        )
    body = res.json()
    assert body["expected_answer"] is None and body["expected_behaviour"] == "smalltalk"
    assert unknown_set.status_code == 404
    assert unknown_set.json()["code"] == "golden_set_not_found"


async def test_a_custom_judge_host_needs_its_own_key() -> None:
    with scoped_app() as (app, world):
        _configure(app, encryption_key=FERNET)
        res = await call(
            app,
            KEY,
            "PUT",
            f"/api/v1/corpora/{world.corpus_a.id}/eval/settings",
            json={"judge_model": "judge-x", "judge_base_url": "https://llm.example.com/v1"},
        )
    assert res.status_code == 422 and res.json()["code"] == "eval_judge_key_required"


async def test_a_judge_host_must_be_an_http_url() -> None:
    with scoped_app() as (app, world):
        _configure(app, encryption_key=FERNET)
        res = await call(
            app,
            KEY,
            "PUT",
            f"/api/v1/corpora/{world.corpus_a.id}/eval/settings",
            json={"judge_api_key": "sk-j", "judge_base_url": "file:///etc/passwd"},
        )
    assert res.status_code == 422
