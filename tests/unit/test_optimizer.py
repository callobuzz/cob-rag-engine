"""Unit tests for Bayesian auto-optimizer (Epic 3-10)."""

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.analytics.optimizer_types import SEARCH_SPACE, ParetoConfig
from callobuzz_rag.db.models.optimization_run import OptimizationRun


def test_optuna_importable() -> None:
    """Verify optuna is installed and importable."""
    import optuna

    study = optuna.create_study(direction="maximize")
    assert study is not None


class TestOptimizationRunModel:
    def test_model_has_required_columns(self) -> None:
        """Verify OptimizationRun model has all expected columns."""
        columns = {c.name for c in OptimizationRun.__table__.columns}
        expected = {
            "id",
            "started_at",
            "completed_at",
            "status",
            "n_trials",
            "pareto_configs",
            "best_config",
            "search_space",
            "error_message",
        }
        assert expected.issubset(columns)

    def test_tablename(self) -> None:
        assert OptimizationRun.__tablename__ == "optimization_runs"

    def test_status_default(self) -> None:
        col = OptimizationRun.__table__.columns["status"]
        assert col.server_default is not None


class TestOptimizerConfig:
    def test_optimizer_defaults(self) -> None:
        """Verify optimizer settings exist with correct defaults."""
        from callobuzz_rag.core.config import Settings

        settings = Settings(
            _env_file=None,
            jina_api_key="test",
            llm_api_key="test",
        )
        assert settings.optimizer_enabled is False
        assert settings.optimizer_n_trials == 50


class TestRetrievalOptimizer:
    def _make_optimizer(self) -> "RetrievalOptimizer":
        from callobuzz_rag.analytics.optimizer import RetrievalOptimizer

        eval_runner = AsyncMock()
        golden_manager = AsyncMock()
        session_factory = MagicMock()

        golden_manager.list_pairs.return_value = [
            {"question": f"Q{i}", "answer": f"A{i}", "id": str(uuid.uuid4())} for i in range(3)
        ]

        call_count = 0

        async def mock_evaluate(
            params: dict[str, Any] | None = None,
            golden_pairs: list[dict[str, Any]] | None = None,
        ) -> dict[str, float]:
            nonlocal call_count
            call_count += 1
            base = 0.5 + (call_count % 10) * 0.04
            return {
                "faithfulness": min(base + 0.1, 1.0),
                "answer_relevancy": min(base, 1.0),
                "latency_p95": 0.5 + (call_count % 5) * 0.2,
            }

        eval_runner.evaluate_with_params.side_effect = mock_evaluate

        return RetrievalOptimizer(
            eval_runner=eval_runner,
            golden_manager=golden_manager,
            session_factory=session_factory,
        )

    def test_constructor(self) -> None:
        optimizer = self._make_optimizer()
        assert optimizer is not None

    def test_search_space_definition(self) -> None:
        optimizer = self._make_optimizer()
        space = optimizer.get_search_space()
        expected_params = {
            "retrieval_top_k_dense",
            "retrieval_top_k_sparse",
            "rrf_k",
            "mmr_lambda",
            "temporal_decay_half_life_days",
            "rerank_top_n_input",
            "rerank_top_k_output",
        }
        assert set(space.keys()) == expected_params

    def test_search_space_ranges(self) -> None:
        optimizer = self._make_optimizer()
        space = optimizer.get_search_space()
        assert space["retrieval_top_k_dense"] == {"low": 10, "high": 100, "type": "int"}
        assert space["mmr_lambda"] == {"low": 0.3, "high": 0.9, "type": "float"}
        assert space["temporal_decay_half_life_days"] == {"low": 7.0, "high": 90.0, "type": "float"}


class TestOptunaStudyIntegration:
    def test_study_creation_multi_objective(self) -> None:
        """Verify Optuna study is created with 3 maximize objectives."""
        import optuna

        study = optuna.create_study(
            directions=["maximize", "maximize", "maximize"],
            sampler=optuna.samplers.NSGAIISampler(seed=42),
        )
        assert len(study.directions) == 3

    def test_param_suggestion_covers_search_space(self) -> None:
        """All search space params are suggested within bounds."""
        import optuna

        study = optuna.create_study(
            directions=["maximize", "maximize", "maximize"],
            sampler=optuna.samplers.NSGAIISampler(seed=42),
        )

        suggested: dict[str, Any] = {}

        def objective(trial: optuna.Trial) -> tuple[float, float, float]:
            for name, spec in SEARCH_SPACE.items():
                if spec.type == "int":
                    val = trial.suggest_int(name, int(spec.low), int(spec.high))
                else:
                    val = trial.suggest_float(name, spec.low, spec.high)
                suggested[name] = val
            return (0.8, 0.7, -0.5)

        study.optimize(objective, n_trials=1)

        for name, spec in SEARCH_SPACE.items():
            assert name in suggested
            assert spec.low <= suggested[name] <= spec.high

    def test_pareto_front_extraction(self) -> None:
        """Verify Pareto front contains non-dominated solutions."""
        import optuna

        study = optuna.create_study(
            directions=["maximize", "maximize", "maximize"],
            sampler=optuna.samplers.NSGAIISampler(seed=42),
        )

        def objective(trial: optuna.Trial) -> tuple[float, float, float]:
            k = trial.suggest_int("retrieval_top_k_dense", 10, 100)
            trial.suggest_int("retrieval_top_k_sparse", 10, 100)
            trial.suggest_int("rrf_k", 20, 120)
            trial.suggest_float("mmr_lambda", 0.3, 0.9)
            trial.suggest_float("temporal_decay_half_life_days", 7.0, 90.0)
            trial.suggest_int("rerank_top_n_input", 20, 100)
            trial.suggest_int("rerank_top_k_output", 3, 20)

            quality = 0.5 + (k / 200)
            latency = -(0.3 + k * 0.01)
            return (quality, quality - 0.05, latency)

        study.optimize(objective, n_trials=20)

        pareto = study.best_trials
        assert len(pareto) >= 1
        for trial in pareto:
            assert len(trial.values) == 3


class TestBalancedConfigSelection:
    def _make_optimizer(self) -> "RetrievalOptimizer":
        from callobuzz_rag.analytics.optimizer import RetrievalOptimizer

        return RetrievalOptimizer(
            eval_runner=AsyncMock(),
            golden_manager=AsyncMock(),
            session_factory=MagicMock(),
        )

    def test_single_config_returns_itself(self) -> None:
        optimizer = self._make_optimizer()
        config = ParetoConfig(
            params={"retrieval_top_k_dense": 50},
            metrics={"faithfulness": 0.9, "answer_relevancy": 0.8, "latency_p95": 0.5},
        )
        result = optimizer._select_balanced_config([config])
        assert result is config

    def test_empty_returns_none(self) -> None:
        optimizer = self._make_optimizer()
        result = optimizer._select_balanced_config([])
        assert result is None

    def test_selects_balanced_not_extreme(self) -> None:
        """Should prefer balanced config over quality-only or speed-only extremes."""
        optimizer = self._make_optimizer()
        configs = [
            ParetoConfig(
                params={"k": 100},
                metrics={
                    "faithfulness": 0.95,
                    "answer_relevancy": 0.90,
                    "latency_p95": 2.0,
                },
                trial_number=0,
            ),
            ParetoConfig(
                params={"k": 50},
                metrics={
                    "faithfulness": 0.85,
                    "answer_relevancy": 0.82,
                    "latency_p95": 0.5,
                },
                trial_number=1,
            ),
            ParetoConfig(
                params={"k": 10},
                metrics={
                    "faithfulness": 0.60,
                    "answer_relevancy": 0.55,
                    "latency_p95": 0.1,
                },
                trial_number=2,
            ),
        ]
        result = optimizer._select_balanced_config(configs)
        assert result is not None
        assert result.trial_number == 1

    def test_ideal_point_distance_logic(self) -> None:
        """With 2 quality objectives vs 1 latency, quality wins ties."""
        optimizer = self._make_optimizer()
        # With only 2 configs, normalized distance favors the one that
        # maximizes both quality dimensions (2/3 ideal) over the one
        # that only minimizes latency (1/3 ideal).
        configs = [
            ParetoConfig(
                params={"k": 80},
                metrics={
                    "faithfulness": 0.70,
                    "answer_relevancy": 0.65,
                    "latency_p95": 0.2,
                },
                trial_number=0,
            ),
            ParetoConfig(
                params={"k": 90},
                metrics={
                    "faithfulness": 0.95,
                    "answer_relevancy": 0.92,
                    "latency_p95": 1.0,
                },
                trial_number=1,
            ),
        ]
        result = optimizer._select_balanced_config(configs)
        assert result is not None
        # Higher quality config preferred (2 quality objectives outweigh 1 latency)
        assert result.trial_number == 1


class TestOptimizeFlow:
    @pytest.mark.asyncio
    async def test_optimize_returns_completed_run(self) -> None:
        """Full optimize() call with mocked eval returns completed OptimizationRun."""
        from callobuzz_rag.analytics.optimizer import RetrievalOptimizer

        eval_runner = AsyncMock()
        golden_manager = AsyncMock()
        session_factory = MagicMock()

        golden_manager.list_pairs.return_value = [
            {"question": "What is RAG?", "answer": "Retrieval-Augmented Generation", "id": "1"},
            {"question": "What is Qdrant?", "answer": "A vector database", "id": "2"},
        ]

        call_count = 0

        async def mock_evaluate(
            params: dict[str, Any] | None = None,
            golden_pairs: list[dict[str, Any]] | None = None,
        ) -> dict[str, float]:
            nonlocal call_count
            call_count += 1
            k = (params or {}).get("retrieval_top_k_dense", 50)
            return {
                "faithfulness": 0.5 + (k / 200),
                "answer_relevancy": 0.4 + (k / 250),
                "latency_p95": 0.2 + k * 0.01,
            }

        eval_runner.evaluate_with_params.side_effect = mock_evaluate

        optimizer = RetrievalOptimizer(
            eval_runner=eval_runner,
            golden_manager=golden_manager,
            session_factory=session_factory,
        )

        run = await optimizer.optimize(n_trials=10)

        assert run.status == "completed"
        assert run.n_trials == 10
        assert run.completed_at is not None
        assert run.pareto_configs is not None
        assert len(run.pareto_configs) >= 1
        assert run.best_config is not None
        assert run.error_message is None
        assert run.search_space is not None

        for config in run.pareto_configs:
            assert "params" in config
            assert "metrics" in config
            assert "faithfulness" in config["metrics"]
            assert "answer_relevancy" in config["metrics"]
            assert "latency_p95" in config["metrics"]

    @pytest.mark.asyncio
    async def test_optimize_fails_without_golden_pairs(self) -> None:
        """optimize() raises ValueError when no golden pairs exist."""
        from callobuzz_rag.analytics.optimizer import RetrievalOptimizer

        eval_runner = AsyncMock()
        golden_manager = AsyncMock()
        golden_manager.list_pairs.return_value = []
        session_factory = MagicMock()

        optimizer = RetrievalOptimizer(
            eval_runner=eval_runner,
            golden_manager=golden_manager,
            session_factory=session_factory,
        )

        with pytest.raises(ValueError, match="No golden pairs"):
            await optimizer.optimize(n_trials=5)

    @pytest.mark.asyncio
    async def test_optimize_handles_eval_failure(self) -> None:
        """optimize() records failure when eval runner raises."""
        from callobuzz_rag.analytics.optimizer import RetrievalOptimizer

        eval_runner = AsyncMock()
        golden_manager = AsyncMock()
        golden_manager.list_pairs.return_value = [
            {"question": "Q1", "answer": "A1", "id": "1"},
        ]
        eval_runner.evaluate_with_params.side_effect = RuntimeError("Eval crashed")
        session_factory = MagicMock()

        optimizer = RetrievalOptimizer(
            eval_runner=eval_runner,
            golden_manager=golden_manager,
            session_factory=session_factory,
        )

        with pytest.raises(RuntimeError, match="Eval crashed"):
            await optimizer.optimize(n_trials=3)


class TestOptimizerDBOperations:
    @pytest.mark.asyncio
    async def test_get_latest_run_returns_none_when_empty(self) -> None:
        """get_latest_run returns None when no optimization runs exist."""
        from callobuzz_rag.analytics.optimizer import RetrievalOptimizer

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        session_factory = MagicMock()
        session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        optimizer = RetrievalOptimizer(
            eval_runner=AsyncMock(),
            golden_manager=AsyncMock(),
            session_factory=session_factory,
        )

        result = await optimizer.get_latest_run()
        assert result is None

    @pytest.mark.asyncio
    async def test_apply_config_rejects_invalid_run(self) -> None:
        """apply_config raises ValueError for non-existent run."""
        from callobuzz_rag.analytics.optimizer import RetrievalOptimizer

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        session_factory = MagicMock()
        session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        optimizer = RetrievalOptimizer(
            eval_runner=AsyncMock(),
            golden_manager=AsyncMock(),
            session_factory=session_factory,
        )

        with pytest.raises(ValueError, match="not found"):
            await optimizer.apply_config("nonexistent-uuid", config_index=0)

    def test_pareto_config_to_dict_roundtrip(self) -> None:
        """ParetoConfig serializes cleanly to dict."""
        config = ParetoConfig(
            params={"retrieval_top_k_dense": 50, "mmr_lambda": 0.7},
            metrics={"faithfulness": 0.9, "answer_relevancy": 0.85, "latency_p95": 0.4},
            trial_number=7,
        )
        d = config.to_dict()
        assert d["params"]["retrieval_top_k_dense"] == 50
        assert d["metrics"]["faithfulness"] == 0.9
        assert d["trial_number"] == 7
