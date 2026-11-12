"""The evaluation add-on (RAGAS + LangChain) is optional: a missing one is one clear error."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.core.exceptions import EvaluationError, RAGKernelError
from callobuzz_rag.db.models import GoldenSet
from callobuzz_rag.evaluation import optional
from callobuzz_rag.evaluation.optional import (
    ADDON_MESSAGE,
    EvaluationAddonMissing,
    ragas_available,
    require_ragas,
)
from tests.unit.scope_harness import call, scoped_app

MISSING = "callobuzz_rag.evaluation.optional.ragas_available"


def test_message_names_the_extra_and_the_docker_arg() -> None:
    assert ADDON_MESSAGE == (
        "Answer-quality evaluation needs the evaluation add-on: "
        "pip install 'cob-rag-engine[eval]' (Docker: build with INSTALL_EVAL=true)"
    )


def test_the_error_is_an_engine_error() -> None:
    assert issubclass(EvaluationAddonMissing, EvaluationError)
    assert issubclass(EvaluationAddonMissing, RAGKernelError)


def test_available_only_when_both_packages_resolve() -> None:
    with patch.object(optional.importlib.util, "find_spec", return_value=object()):
        assert ragas_available() is True
    names = {"ragas": object(), "langchain_core": None}
    with patch.object(optional.importlib.util, "find_spec", side_effect=names.get):
        assert ragas_available() is False
    with patch.object(optional.importlib.util, "find_spec", return_value=None):
        assert ragas_available() is False


def test_require_raises_with_the_message_when_missing() -> None:
    with patch(MISSING, return_value=False), pytest.raises(EvaluationAddonMissing) as err:
        require_ragas()
    assert str(err.value) == ADDON_MESSAGE
    with patch(MISSING, return_value=True):
        require_ragas()


def test_the_traffic_scorer_refuses_without_the_add_on() -> None:
    from callobuzz_rag.evaluation.runner import _run_ragas_evaluate

    with patch(MISSING, return_value=False), pytest.raises(EvaluationAddonMissing):
        _run_ragas_evaluate([{"user_input": "q", "response": "a", "retrieved_contexts": ["c"]}])


def test_the_golden_scorer_refuses_without_the_add_on() -> None:
    from callobuzz_rag.evaluation.scoring import ScoringSample, score_samples

    with patch(MISSING, return_value=False), pytest.raises(EvaluationAddonMissing):
        score_samples([ScoringSample("q", "a", ["c"], None)], SimpleNamespace(), SimpleNamespace())


async def test_admin_trigger_is_refused_with_a_coded_error() -> None:
    with (
        scoped_app() as (app, _),
        patch("callobuzz_rag.api.routes.admin.ragas_available", return_value=False),
    ):
        res = await call(app, "admin", "POST", "/api/v1/admin/eval/trigger", json={})
    assert res.status_code == 501
    assert res.json() == {"detail": ADDON_MESSAGE, "code": "evaluation_addon_missing"}


async def test_info_reports_whether_ragas_is_installed() -> None:
    with scoped_app() as (app, _):
        with patch("callobuzz_rag.api.routes.identity.ragas_available", return_value=False):
            off = await call(app, "admin", "GET", "/api/v1/info")
        with patch("callobuzz_rag.api.routes.identity.ragas_available", return_value=True):
            on = await call(app, "admin", "GET", "/api/v1/info")
    assert off.json()["features"]["eval_ragas"] is False
    assert on.json()["features"]["eval_ragas"] is True


class _Result:
    def __init__(self, first=None, rows=None) -> None:
        self._first, self._rows = first, rows if rows is not None else []

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._first

    def scalar(self):
        return len(self._rows)


async def test_a_full_run_with_no_criteria_needs_the_add_on() -> None:
    with scoped_app() as (app, world):
        golden = GoldenSet(id=uuid.uuid4(), corpus_id=world.corpus_a.id, name="B", source="owner")
        world.session.execute = AsyncMock(
            side_effect=[_Result(golden), _Result(None), _Result(rows=[])]
        )
        with patch("callobuzz_rag.api.routes.evals.ragas_available", return_value=False):
            res = await call(
                app,
                "owner_a",
                "POST",
                f"/api/v1/corpora/{world.corpus_a.id}/eval/runs",
                json={"set_id": str(golden.id), "mode": "full"},
            )
    assert res.status_code == 501
    assert res.json()["code"] == "evaluation_addon_missing"


async def test_scheduled_traffic_eval_is_skipped_with_an_info_log(caplog) -> None:
    from callobuzz_rag.evaluation.scheduler import run_scheduled_eval

    with (
        patch("callobuzz_rag.evaluation.scheduler.ragas_available", return_value=False),
        patch("callobuzz_rag.evaluation.scheduler.asyncio") as aio,
        caplog.at_level("INFO"),
    ):
        result = run_scheduled_eval()
    aio.run.assert_not_called()
    assert result == {"status": "skipped", "reason": "evaluation_addon_missing"}
    assert any(
        r.levelname == "INFO" and "evaluation add-on" in r.getMessage() for r in caplog.records
    )


async def test_optimisation_trigger_is_refused_too() -> None:
    # Its objective is the RAGAS faithfulness and answer-relevancy scores.
    with (
        scoped_app() as (app, _),
        patch("callobuzz_rag.api.routes.admin.ragas_available", return_value=False),
    ):
        res = await call(app, "admin", "POST", "/api/v1/admin/optimize/trigger", json={})
    assert res.status_code == 501
    assert res.json()["code"] == "evaluation_addon_missing"
