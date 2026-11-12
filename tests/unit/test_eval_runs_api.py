"""Starting runs: cost confirmation, one run per set, cap, filters on samples."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.db.models import EvalRun, EvalRunSample, GoldenSet
from callobuzz_rag.evaluation.cost import CostEstimate
from tests.unit.scope_harness import call, scoped_app

pytestmark = pytest.mark.usefixtures("unlimited_plan")

KEY = "owner_a"
EST = CostEstimate(10, 0.03, 0.08, 0.11, [], {})
JUDGE = SimpleNamespace(model="judge-x", provider="openrouter", api_key="k", base_url=None)
ESTIMATE = "callobuzz_rag.api.routes.evals._estimate"
JUDGE_CFG = "callobuzz_rag.evaluation.golden_runner.judge_config_for"
DISPATCH = "callobuzz_rag.evaluation.golden_runner._dispatch"


class _Result:
    def __init__(self, first: Any = None, rows: list[Any] | None = None) -> None:
        self._first = first
        self._rows = rows if rows is not None else ([] if first is None else [first])

    def scalars(self) -> "_Result":
        return self

    def all(self) -> list[Any]:
        return self._rows

    def scalar_one_or_none(self) -> Any:
        return self._first

    def scalar(self) -> int:
        return len(self._rows)


def _set(world: Any) -> GoldenSet:
    return GoldenSet(id=uuid.uuid4(), corpus_id=world.corpus_a.id, name="Basics", source="owner")


def _queries(world: Any, *results: _Result) -> None:
    world.session.execute = AsyncMock(side_effect=list(results))

    async def refresh(obj: Any, *a: Any, **k: Any) -> None:
        for name, value in (("id", uuid.uuid4()), ("started_at", datetime.now(UTC))):
            if hasattr(obj, name) and getattr(obj, name, None) is None:
                setattr(obj, name, value)

    world.session.refresh = AsyncMock(side_effect=refresh)


def _url(world: Any, tail: str) -> str:
    return f"/api/v1/corpora/{world.corpus_a.id}/eval{tail}"


async def test_full_run_without_confirmation_is_409() -> None:
    with scoped_app() as (app, world):
        golden = _set(world)
        _queries(world, _Result(golden), _Result(None))
        with (
            patch(ESTIMATE, AsyncMock(return_value=(EST, "answer-model", "judge-x"))),
            patch(JUDGE_CFG, AsyncMock(return_value=JUDGE)),
        ):
            res = await call(
                app,
                KEY,
                "POST",
                _url(world, "/runs"),
                json={"set_id": str(golden.id), "mode": "full"},
            )
    assert res.status_code == 409
    assert res.json() == {
        "detail": "Confirm the estimated cost to start this run",
        "code": "eval_cost_unconfirmed",
        "estimated_cost_usd": 0.11,
    }


async def test_confirmed_full_run_is_queued_with_its_snapshot_and_thresholds() -> None:
    with scoped_app() as (app, world):
        golden = _set(world)
        criterion = SimpleNamespace(name="mentions-fee", definition="States the fee", threshold=0.8)
        _queries(world, _Result(golden), _Result(None), _Result(rows=[criterion]))
        with (
            patch(ESTIMATE, AsyncMock(return_value=(EST, "answer-model", "judge-x"))),
            patch(JUDGE_CFG, AsyncMock(return_value=JUDGE)),
            patch(DISPATCH) as dispatch,
        ):
            res = await call(
                app,
                KEY,
                "POST",
                _url(world, "/runs"),
                json={
                    "set_id": str(golden.id),
                    "mode": "full",
                    "name": "rerank on",
                    "confirm_cost_usd": 0.11,
                    "settings": {"assistant": "draft", "retrieval": {"reranking_enabled": True}},
                },
            )
        assert res.status_code == 202, res.text
        run = next(
            c.args[0] for c in world.session.add.call_args_list if isinstance(c.args[0], EvalRun)
        )
    body = res.json()
    assert body["status"] == "queued" and body["name"] == "rerank on"
    assert body["estimated_cost_usd"] == 0.11
    assert dispatch.call_count == 1
    assert run.config_snapshot["assistant"] == "draft"
    assert run.thresholds["criterion:mentions-fee"] == 0.8
    assert run.criteria == [
        {"name": "mentions-fee", "definition": "States the fee", "threshold": 0.8}
    ]
    assert "owner@tenant-a.example" in run.created_by


async def test_fast_run_needs_no_confirmation_and_no_judge() -> None:
    with scoped_app() as (app, world):
        golden = _set(world)
        _queries(world, _Result(golden), _Result(None))
        with patch(DISPATCH) as dispatch:
            res = await call(
                app,
                KEY,
                "POST",
                _url(world, "/runs"),
                json={"set_id": str(golden.id), "mode": "fast"},
            )
    assert res.status_code == 202, res.text
    assert dispatch.call_count == 1 and res.json()["estimated_cost_usd"] == 0.0


async def test_busy_set_is_409() -> None:
    busy = uuid.uuid4()
    with scoped_app() as (app, world):
        golden = _set(world)
        _queries(world, _Result(golden), _Result(busy))
        res = await call(
            app, KEY, "POST", _url(world, "/runs"), json={"set_id": str(golden.id), "mode": "fast"}
        )
    assert res.status_code == 409 and res.json()["code"] == "eval_run_in_progress"
    assert res.json()["run_id"] == str(busy)


async def test_a_full_run_with_no_judge_credentials_is_503() -> None:
    with scoped_app() as (app, world):
        golden = _set(world)
        _queries(world, _Result(golden), _Result(None))
        nobody = SimpleNamespace(model="m", provider="p", api_key=None, base_url=None)
        with patch(JUDGE_CFG, AsyncMock(return_value=nobody)):
            res = await call(
                app,
                KEY,
                "POST",
                _url(world, "/runs"),
                json={"set_id": str(golden.id), "mode": "full", "confirm_cost_usd": 1},
            )
    assert res.status_code == 503 and res.json()["code"] == "eval_judge_unavailable"


async def test_an_unknown_preset_is_422_at_creation() -> None:
    with scoped_app() as (app, world):
        golden = _set(world)
        _queries(world, _Result(golden), _Result(None))
        res = await call(
            app,
            KEY,
            "POST",
            _url(world, "/runs"),
            json={
                "set_id": str(golden.id),
                "mode": "fast",
                "settings": {"retrieval": {"preset": "turbo"}},
            },
        )
    assert res.status_code == 422


async def test_a_full_run_larger_than_the_monthly_room_is_429() -> None:
    from callobuzz_rag.accounts import caps

    with scoped_app() as (app, world):
        golden = _set(world)
        _queries(world, _Result(golden), _Result(None))
        with (
            patch(ESTIMATE, AsyncMock(return_value=(EST, "answer-model", "judge-x"))),
            patch(JUDGE_CFG, AsyncMock(return_value=JUDGE)),
            patch.object(caps, "question_quota", AsyncMock(return_value=(100, 95))),
        ):
            res = await call(
                app,
                KEY,
                "POST",
                _url(world, "/runs"),
                json={"set_id": str(golden.id), "mode": "full", "confirm_cost_usd": 0.11},
            )
    assert res.status_code == 429 and res.json()["code"] == "question_cap_reached"
    assert res.json()["used"] == 95 and res.json()["limit"] == 100


async def test_compare_needs_runs_of_one_set() -> None:
    with scoped_app() as (app, world):
        a = SimpleNamespace(
            id=uuid.uuid4(),
            corpus_id=world.corpus_a.id,
            set_id=uuid.uuid4(),
            kind="golden",
            status="completed",
        )
        b = SimpleNamespace(
            id=uuid.uuid4(),
            corpus_id=world.corpus_a.id,
            set_id=uuid.uuid4(),
            kind="golden",
            status="completed",
        )
        _queries(world, _Result(rows=[a, b]))
        res = await call(
            app, KEY, "POST", _url(world, "/compare"), json={"run_ids": [str(a.id), str(b.id)]}
        )
    assert res.status_code == 422 and res.json()["code"] == "eval_compare_invalid"


async def test_compare_unknown_run_is_404() -> None:
    with scoped_app() as (app, world):
        _queries(world, _Result(rows=[]))
        res = await call(
            app,
            KEY,
            "POST",
            _url(world, "/compare"),
            json={"run_ids": [str(uuid.uuid4()), str(uuid.uuid4())]},
        )
    assert res.status_code == 404 and res.json()["code"] == "eval_run_not_found"


async def test_compare_needs_two_to_four_runs() -> None:
    with scoped_app() as (app, world):
        res = await call(
            app, KEY, "POST", _url(world, "/compare"), json={"run_ids": [str(uuid.uuid4())]}
        )
    assert res.status_code == 422


async def test_rating_a_sample_records_who_rated() -> None:
    with scoped_app() as (app, world):
        sample = SimpleNamespace(
            id=uuid.uuid4(),
            golden_pair_id=None,
            question="q",
            expected_answer=None,
            answer="a",
            contexts=[],
            scores={},
            reasons={},
            behaviour=None,
            tags={},
            passed=None,
            latency_ms=None,
            cost_usd=None,
            rating=None,
            rated_by=None,
            error=None,
        )
        _queries(world, _Result(sample))
        res = await call(
            app, KEY, "PATCH", _url(world, f"/samples/{sample.id}"), json={"rating": "poor"}
        )
    assert res.status_code == 200, res.text
    assert res.json()["rating"] == "poor" and "owner@tenant-a.example" in res.json()["rated_by"]


async def test_a_sample_filter_names_a_tag_as_name_colon_value() -> None:
    with scoped_app() as (app, world):
        run = SimpleNamespace(id=uuid.uuid4())
        _queries(world, _Result(run), _Result(rows=[]), _Result(rows=[]))
        bad = await call(app, KEY, "GET", _url(world, f"/runs/{run.id}/samples?tag=colour:red"))
    assert bad.status_code == 422


async def test_samples_report_total_and_page() -> None:
    with scoped_app() as (app, world):
        run = SimpleNamespace(id=uuid.uuid4())
        sample = EvalRunSample(
            id=uuid.uuid4(),
            run_id=run.id,
            question="q",
            contexts=[],
            scores={"faithfulness": 0.4},
            reasons={},
            tags={},
            position=0,
        )
        _queries(world, _Result(run), _Result(rows=[sample]), _Result(rows=[sample]))
        res = await call(
            app,
            KEY,
            "GET",
            _url(world, f"/runs/{run.id}/samples?failed_only=true&metric=faithfulness&below=0.5"),
        )
    assert res.status_code == 200, res.text
    assert res.json()["count"] == 1 and res.json()["items"][0]["scores"] == {"faithfulness": 0.4}


async def test_publishing_queues_the_free_behaviour_check() -> None:
    from tests.unit.test_assistant_routes import STORE, _row

    with scoped_app() as (app, world):
        cid = world.corpus("a").id
        published = _row(cid, 4, "published")
        with (
            patch(STORE) as store,
            patch(
                "callobuzz_rag.evaluation.golden_runner.enqueue_publish_runs",
                AsyncMock(return_value=1),
            ) as enqueue,
        ):
            store.return_value.publish = AsyncMock(return_value=published)
            store.return_value.pending_card = None
            res = await call(
                app,
                KEY,
                "POST",
                f"/api/v1/corpora/{cid}/assistant/publish",
                json={"base_revision": 2},
            )
    assert res.status_code == 200, res.text
    enqueue.assert_awaited_once()
    assert enqueue.await_args.args[1:] == (cid, 4)


async def test_a_failing_enqueue_never_fails_the_publish() -> None:
    from tests.unit.test_assistant_routes import STORE, _row

    with scoped_app() as (app, world):
        cid = world.corpus("a").id
        with (
            patch(STORE) as store,
            patch(
                "callobuzz_rag.evaluation.golden_runner.enqueue_publish_runs",
                AsyncMock(side_effect=RuntimeError("broker down")),
            ),
        ):
            store.return_value.publish = AsyncMock(return_value=_row(cid, 4, "published"))
            store.return_value.pending_card = None
            res = await call(
                app,
                KEY,
                "POST",
                f"/api/v1/corpora/{cid}/assistant/publish",
                json={"base_revision": 2},
            )
    assert res.status_code == 200, res.text


async def test_a_switched_off_assistant_queues_nothing() -> None:
    from tests.unit.test_assistant_routes import STORE, _row

    with scoped_app() as (app, world):
        cid = world.corpus("a").id
        with (
            patch(STORE) as store,
            patch(
                "callobuzz_rag.evaluation.golden_runner.enqueue_publish_runs", AsyncMock()
            ) as enqueue,
        ):
            store.return_value.publish = AsyncMock(
                return_value=_row(cid, 5, "published", enabled=False)
            )
            store.return_value.pending_card = None
            res = await call(
                app,
                KEY,
                "POST",
                f"/api/v1/corpora/{cid}/assistant/publish",
                json={"base_revision": 2},
            )
    assert res.status_code == 200, res.text
    enqueue.assert_not_awaited()


async def test_compare_refuses_the_same_run_twice() -> None:
    with scoped_app() as (app, world):
        a = SimpleNamespace(
            id=uuid.uuid4(),
            corpus_id=world.corpus_a.id,
            set_id=uuid.uuid4(),
            kind="golden",
            status="completed",
        )
        _queries(world, _Result(rows=[a]))
        res = await call(
            app, KEY, "POST", _url(world, "/compare"), json={"run_ids": [str(a.id), str(a.id)]}
        )
    assert res.status_code == 422 and res.json()["code"] == "eval_compare_invalid"


async def test_compare_refuses_runs_whose_set_was_deleted() -> None:
    with scoped_app() as (app, world):
        runs = [
            SimpleNamespace(
                id=uuid.uuid4(),
                corpus_id=world.corpus_a.id,
                set_id=None,
                kind="golden",
                status="completed",
            )
            for _ in range(2)
        ]
        _queries(world, _Result(rows=runs))
        res = await call(
            app,
            KEY,
            "POST",
            _url(world, "/compare"),
            json={"run_ids": [str(r.id) for r in runs]},
        )
    assert res.status_code == 422 and res.json()["code"] == "eval_compare_invalid"


@pytest.mark.parametrize(("key", "admin"), [("admin", True), ("sa", True), (KEY, False)])
async def test_a_run_the_admin_starts_is_marked_so_the_customer_is_not_billed(
    key: str, admin: bool
) -> None:
    with scoped_app() as (app, world):
        golden = _set(world)
        _queries(world, _Result(golden), _Result(None))
        with patch(DISPATCH):
            res = await call(
                app,
                key,
                "POST",
                _url(world, "/runs"),
                json={"set_id": str(golden.id), "mode": "fast"},
            )
        assert res.status_code == 202, res.text
        run = next(
            c.args[0] for c in world.session.add.call_args_list if isinstance(c.args[0], EvalRun)
        )
    assert run.config_snapshot.get("admin_initiated", False) is admin
