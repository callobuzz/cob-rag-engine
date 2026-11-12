"""A retrieval parameter set evaluated against golden questions (the optimizer's trial).

`EvaluationRunner.evaluate_with_params` is what `RetrievalOptimizer` calls once per trial. These
tests use fakes for the pipeline and the judge, so they need neither Qdrant nor a model; the
default scorer needs the optional evaluation add-on, and the test that runs it skips without it.
"""

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.exceptions import EvaluationAddonMissing, EvaluationError
from callobuzz_rag.evaluation.param_eval import ParamEvaluator, TrialAnswer
from callobuzz_rag.evaluation.runner import EvaluationRunner

CORPUS = uuid.uuid4()


def _pair(question: str, corpus_id: uuid.UUID = CORPUS) -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(), corpus_id=corpus_id, query=question, expected_answer=f"A to {question}"
    )


def _settings() -> Settings:
    return Settings(_env_file=None)


class FakeTrial:
    """Stands in for one corpus's pipeline: records the settings and answers deterministically."""

    def __init__(self, settings: Settings, corpus_id: uuid.UUID, log: list[Any]) -> None:
        self.settings = settings
        self.corpus_id = corpus_id
        self.judge_config = MagicMock(name="judge")
        self.log = log

    async def answer(self, pair: Any) -> TrialAnswer:
        self.log.append((self.corpus_id, pair.query, self.settings.rrf_k))
        if "boom" in pair.query:
            raise RuntimeError("search is down")
        return TrialAnswer(answer=f"answer {pair.query}", contexts=[f"ctx {pair.query}"])


def _evaluator(log: list[Any], scorer: Any = None, **kwargs: Any) -> ParamEvaluator:
    @asynccontextmanager
    async def open_trial(settings: Settings, corpus_id: uuid.UUID, params: dict[str, Any]) -> Any:
        yield FakeTrial(settings, corpus_id, log)

    def default_scorer(samples: list[Any], judge_config: Any, settings: Any) -> list[dict]:
        return [{"faithfulness": 0.8, "answer_relevancy": 0.6} for _ in samples]

    return ParamEvaluator(
        settings=_settings(),
        open_trial=open_trial,
        scorer=scorer or default_scorer,
        **kwargs,
    )


class TestEvaluate:
    async def test_returns_the_three_objectives(self) -> None:
        log: list[Any] = []
        metrics = await _evaluator(log).evaluate(
            {"rrf_k": 33, "mmr_lambda": 0.4}, [_pair("one"), _pair("two")]
        )
        assert metrics["faithfulness"] == pytest.approx(0.8)
        assert metrics["answer_relevancy"] == pytest.approx(0.6)
        assert metrics["latency_p95"] >= 0.0
        assert metrics["questions"] == 2

    async def test_the_trial_runs_with_the_parameters_applied(self) -> None:
        log: list[Any] = []
        await _evaluator(log).evaluate({"rrf_k": 33}, [_pair("one")])
        assert log == [(CORPUS, "one", 33)]  # the copy of the settings the pipeline sees

    async def test_does_not_change_the_engine_settings(self) -> None:
        settings = _settings()
        before = settings.rrf_k
        evaluator = _evaluator([])
        evaluator._settings = settings
        await evaluator.evaluate({"rrf_k": before + 5}, [_pair("one")])
        assert settings.rrf_k == before

    async def test_questions_are_grouped_by_knowledge_base(self) -> None:
        other = uuid.uuid4()
        log: list[Any] = []
        await _evaluator(log).evaluate({}, [_pair("a"), _pair("b", other), _pair("c")])
        assert sorted(q for _, q, _ in log) == ["a", "b", "c"]
        assert {c for c, _, _ in log} == {CORPUS, other}

    async def test_latency_p95_comes_from_the_measured_question_times(self) -> None:
        ticks = iter([0.0, 1.0, 1.0, 4.0])  # two questions: 1 s and 3 s

        metrics = await _evaluator([], clock=lambda: next(ticks)).evaluate(
            {}, [_pair("a"), _pair("b")]
        )
        assert metrics["latency_p95"] == pytest.approx(2.9)  # linear interpolation: p95 of [1, 3]

    async def test_a_failed_question_is_skipped_and_counted(self) -> None:
        metrics = await _evaluator([]).evaluate({}, [_pair("a"), _pair("boom")])
        assert metrics["questions"] == 1
        assert metrics["failed_questions"] == 1

    async def test_every_question_failing_is_an_error(self) -> None:
        with pytest.raises(EvaluationError, match="No golden question"):
            await _evaluator([]).evaluate({}, [_pair("boom")])

    async def test_a_judge_that_scores_nothing_is_an_error(self) -> None:
        def blank(samples: list[Any], judge_config: Any, settings: Any) -> list[dict]:
            return [{"faithfulness": None, "answer_relevancy": None} for _ in samples]

        with pytest.raises(EvaluationError, match="scored"):
            await _evaluator([], scorer=blank).evaluate({}, [_pair("a")])

    async def test_unscored_questions_do_not_drag_the_mean_down(self) -> None:
        scores = iter([{"faithfulness": 1.0, "answer_relevancy": 0.5}, {}])

        def uneven(samples: list[Any], judge_config: Any, settings: Any) -> list[dict]:
            return [next(scores) for _ in samples]

        metrics = await _evaluator([], scorer=uneven).evaluate({}, [_pair("a"), _pair("b")])
        assert metrics["faithfulness"] == pytest.approx(1.0)
        assert metrics["answer_relevancy"] == pytest.approx(0.5)

    async def test_an_unknown_parameter_is_refused(self) -> None:
        with pytest.raises(EvaluationError, match="not_a_setting"):
            await _evaluator([]).evaluate({"not_a_setting": 1}, [_pair("a")])

    async def test_no_golden_questions_is_an_error(self) -> None:
        with pytest.raises(EvaluationError, match="No golden question"):
            await _evaluator([]).evaluate({}, [])


class TestAddon:
    async def test_the_default_scorer_needs_the_evaluation_addon(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from callobuzz_rag.evaluation import optional

        monkeypatch.setattr(optional, "ragas_available", lambda: False)
        evaluator = ParamEvaluator(settings=_settings(), open_trial=MagicMock())
        with pytest.raises(EvaluationAddonMissing):
            await evaluator.evaluate({}, [_pair("a")])


class TestRunnerDelegates:
    async def test_evaluate_with_params_runs_the_evaluator(self) -> None:
        evaluator = _evaluator([])
        runner = EvaluationRunner(session_factory=MagicMock(), param_evaluator=evaluator)
        metrics = await runner.evaluate_with_params(params={"rrf_k": 40}, golden_pairs=[_pair("q")])
        assert set(metrics) >= {"faithfulness", "answer_relevancy", "latency_p95"}


class TestOptimizerEndToEnd:
    """The optimizer against the real runner: the call that used to be missing."""

    async def test_a_run_completes_with_a_pareto_front(self) -> None:
        pytest.importorskip("optuna")
        from callobuzz_rag.analytics.optimizer import RetrievalOptimizer
        from callobuzz_rag.analytics.optimizer_types import SEARCH_SPACE

        seen: list[dict[str, Any]] = []

        @asynccontextmanager
        async def open_trial(
            settings: Settings, corpus_id: uuid.UUID, params: dict[str, Any]
        ) -> Any:
            seen.append(params)
            yield FakeTrial(settings, corpus_id, [])

        def scorer(samples: list[Any], judge_config: Any, settings: Settings) -> list[dict]:
            # Quality rises with the dense width, so the front has a real trade-off to show.
            width = seen[-1]["retrieval_top_k_dense"]
            return [
                {"faithfulness": 0.5 + width / 250, "answer_relevancy": 0.4 + width / 300}
                for _ in samples
            ]

        evaluator = ParamEvaluator(settings=_settings(), open_trial=open_trial, scorer=scorer)
        runner = EvaluationRunner(session_factory=MagicMock(), param_evaluator=evaluator)

        golden = MagicMock()

        async def list_pairs(**_: Any) -> list[Any]:
            return [_pair("one"), _pair("two")]

        golden.list_pairs = list_pairs
        run = SimpleNamespace(id=uuid.uuid4(), status="queued", pareto_configs=None)

        optimizer = RetrievalOptimizer(
            eval_runner=runner, golden_manager=golden, session_factory=MagicMock()
        )

        async def begin(**_: Any) -> Any:
            return run

        async def save(_: Any) -> None:
            return None

        optimizer._begin_run = begin  # type: ignore[method-assign]
        optimizer._save_run = save  # type: ignore[method-assign]

        result = await optimizer.optimize(n_trials=6)

        assert result.status == "completed"
        assert len(seen) == 6  # every trial evaluated
        assert set(seen[0]) == set(SEARCH_SPACE)  # with the whole search space applied
        assert result.pareto_configs
        assert {"faithfulness", "answer_relevancy", "latency_p95"} <= set(
            result.pareto_configs[0]["metrics"]
        )
        assert result.best_config is not None


class TestDefaultScorer:
    def test_objectives_only_builds_just_the_two_metrics(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pytest.importorskip("ragas")
        pytest.importorskip("langchain_core")
        from callobuzz_rag.evaluation import scoring

        built: list[list[str]] = []

        def fake_evaluate(dataset: Any, metrics: list[Any], **_: Any) -> Any:
            built.append([m.name for m in metrics])
            row = {"faithfulness": 0.9, "answer_relevancy": 0.7}
            return SimpleNamespace(scores=[row for _ in dataset.samples])

        class NoEmbeddings:
            ragas = None

            def __init__(self, settings: Any) -> None: ...

            def __enter__(self) -> "NoEmbeddings":
                return self

            def __exit__(self, *exc: Any) -> None: ...

        monkeypatch.setattr(scoring, "_evaluate", fake_evaluate)
        monkeypatch.setattr(scoring, "RagasEmbeddings", NoEmbeddings)
        monkeypatch.setattr(scoring, "build_ragas_llm", lambda *a, **k: None)

        out = scoring.score_samples(
            [scoring.ScoringSample("q", "a", ["c"], "reference")],
            MagicMock(),
            _settings(),
            objectives_only=True,
        )
        assert built == [["faithfulness", "answer_relevancy"]]
        assert out[0]["faithfulness"] == 0.9
        assert out[0]["answer_relevancy"] == 0.7
