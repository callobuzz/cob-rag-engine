"""Bayesian multi-objective optimizer for retrieval parameters.

Uses Optuna with NSGAIISampler to explore retrieval parameter space,
evaluating each configuration against golden datasets via RAGAS metrics.
Produces a Pareto front of quality-vs-latency trade-offs.

Dependencies: Epic 3-05 (eval runner), Epic 3-06 (golden dataset manager).
"""

import logging
from datetime import UTC, datetime
from typing import Any

from callobuzz_rag.analytics.optimizer_types import (
    SEARCH_SPACE,
    ParetoConfig,
)

logger = logging.getLogger(__name__)


class RetrievalOptimizer:
    """Multi-objective Bayesian optimizer for retrieval parameters.

    Explores a defined search space using Optuna's NSGA-II sampler,
    evaluating each parameter configuration against golden Q&A pairs
    via RAGAS metrics. Stores Pareto-optimal configs in the DB.
    """

    def __init__(
        self,
        eval_runner: Any,
        golden_manager: Any,
        session_factory: Any,
        webhook_dispatcher: Any | None = None,
    ) -> None:
        self._eval_runner = eval_runner
        self._golden_manager = golden_manager
        self._session_factory = session_factory
        self._webhook_dispatcher = webhook_dispatcher

    async def _fire_optimization_complete(
        self,
        run_id: str,
        best_config: dict[str, Any],
        pareto_size: int,
        n_trials: int,
    ) -> None:
        """Fire webhook after optimization run completes."""
        if self._webhook_dispatcher is None:
            return

        try:
            from callobuzz_rag.core.events import WebhookEvent

            await self._webhook_dispatcher.dispatch(
                WebhookEvent.OPTIMIZATION_COMPLETE,
                {
                    "run_id": run_id,
                    "best_config": best_config,
                    "pareto_size": pareto_size,
                    "n_trials": n_trials,
                },
            )
        except Exception:
            logger.warning(
                "Webhook dispatch failed for OPTIMIZATION_COMPLETE (run=%s)",
                run_id,
                exc_info=True,
            )

    def get_search_space(self) -> dict[str, dict[str, Any]]:
        """Return the parameter search space definition."""
        return {
            name: {"low": param.low, "high": param.high, "type": param.type}
            for name, param in SEARCH_SPACE.items()
        }

    async def optimize(
        self,
        n_trials: int = 50,
        corpus_id: Any | None = None,
        run_id: Any | None = None,
    ) -> Any:
        """Run multi-objective optimization over retrieval parameters.

        The run is persisted from the start (status "running") and updated when
        it completes or fails, so it can be polled by id.

        Args:
            n_trials: Number of Optuna trials to run.
            corpus_id: Optional corpus to scope optimization to. If None, uses all pairs.
            run_id: A row the API already created (status "queued") and returned to
                its caller. Without it a new row is created.

        Returns:
            OptimizationRun DB model with Pareto configs and best config.
        """
        import optuna

        run = await self._begin_run(n_trials=n_trials, corpus_id=corpus_id, run_id=run_id)

        try:
            if corpus_id is not None:
                golden_pairs = await self._golden_manager.list_by_corpus(
                    corpus_id, reference_only=True
                )
            else:
                golden_pairs = await self._golden_manager.list_pairs(reference_only=True)
            if not golden_pairs:
                raise ValueError("No golden pairs available — cannot optimize without a benchmark")

            logger.info(
                "Starting optimization: %d trials, %d golden pairs",
                n_trials,
                len(golden_pairs),
            )

            study = optuna.create_study(
                directions=["maximize", "maximize", "maximize"],
                sampler=optuna.samplers.NSGAIISampler(seed=42),
                study_name="rag-retrieval-optimization",
            )

            # ask/tell keeps every trial in this event loop: the evaluation opens database
            # sessions and network clients that belong to it (a loop per trial in a worker
            # thread, as this used to do, cannot share them).
            optuna.logging.set_verbosity(optuna.logging.WARNING)
            for _ in range(n_trials):
                trial = study.ask()
                params = self._suggest_params(trial)
                try:
                    metrics = await self._eval_runner.evaluate_with_params(
                        params=params,
                        golden_pairs=golden_pairs,
                    )
                except BaseException:
                    study.tell(trial, state=optuna.trial.TrialState.FAIL)
                    raise
                study.tell(
                    trial,
                    [
                        metrics["faithfulness"],
                        metrics["answer_relevancy"],
                        -metrics["latency_p95"],
                    ],
                )

            pareto_configs = self._extract_pareto_configs(study)
            best_config = self._select_balanced_config(pareto_configs)

            run.pareto_configs = [pc.to_dict() for pc in pareto_configs]
            run.best_config = best_config.to_dict() if best_config else None
            run.status = "completed"
            run.completed_at = datetime.now(UTC)
            await self._save_run(run)

            logger.info(
                "Optimization complete: %d Pareto-optimal configs found",
                len(pareto_configs),
            )

            # Fire webhook on completion
            await self._fire_optimization_complete(
                run_id=str(run.id),
                best_config=best_config.to_dict() if best_config else {},
                pareto_size=len(pareto_configs),
                n_trials=n_trials,
            )

        except Exception as exc:
            run.status = "failed"
            run.error_message = str(exc)
            run.completed_at = datetime.now(UTC)
            await self._save_run(run)
            logger.exception("Optimization failed")
            raise

        return run

    async def _begin_run(self, *, n_trials: int, corpus_id: Any | None, run_id: Any | None) -> Any:
        """Pick up the queued row (or create one) and mark it running."""
        from callobuzz_rag.db.models.optimization_run import OptimizationRun

        async with self._session_factory() as session:
            run = await session.get(OptimizationRun, run_id) if run_id is not None else None
            if run is None:
                if run_id is not None:
                    logger.warning("Optimization run %s not found; recording a new run", run_id)
                run = OptimizationRun(corpus_id=corpus_id)
                session.add(run)
            run.n_trials = n_trials
            run.search_space = self.get_search_space()
            run.status = "running"
            await session.commit()
            await session.refresh(run)
        return run

    async def _save_run(self, run: Any) -> None:
        """Write the run's current state; a failed write is logged, not raised."""
        try:
            async with self._session_factory() as session:
                await session.merge(run)
                await session.commit()
        except Exception:
            logger.warning("Could not save optimization run %s", run.id, exc_info=True)

    async def get_latest_run(self) -> Any | None:
        """Fetch the most recent OptimizationRun from the DB."""
        from sqlalchemy import select

        from callobuzz_rag.db.models.optimization_run import OptimizationRun

        async with self._session_factory() as session:
            stmt = select(OptimizationRun).order_by(OptimizationRun.started_at.desc()).limit(1)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def apply_config(self, run_id: str, config_index: int = 0) -> dict[str, Any]:
        """Apply a Pareto-optimal config from a completed run to settings.

        Args:
            run_id: UUID of the OptimizationRun.
            config_index: Index into pareto_configs list (0 = best_config).

        Returns:
            The applied config dict.
        """
        from sqlalchemy import select

        from callobuzz_rag.db.models.config import Config
        from callobuzz_rag.db.models.optimization_run import OptimizationRun

        async with self._session_factory() as session:
            stmt = select(OptimizationRun).where(OptimizationRun.id == run_id)
            result = await session.execute(stmt)
            run = result.scalar_one_or_none()

            if run is None:
                raise ValueError(f"OptimizationRun not found: {run_id}")
            if run.status != "completed":
                raise ValueError(f"Run {run_id} is not completed (status={run.status})")
            if not run.pareto_configs:
                raise ValueError(f"Run {run_id} has no Pareto configs")
            if config_index < 0 or config_index >= len(run.pareto_configs):
                raise ValueError(
                    f"config_index {config_index} out of range (0-{len(run.pareto_configs) - 1})"
                )

            config = run.pareto_configs[config_index]
            params: dict[str, Any] = config.get("params", config)
            for key, value in params.items():
                config_key = f"retrieval.{key}"
                existing = await session.execute(select(Config).where(Config.key == config_key))
                row = existing.scalar_one_or_none()
                json_value = {"value": value}
                if row:
                    row.value = json_value
                else:
                    session.add(Config(key=config_key, value=json_value))

            await session.commit()
            logger.info("Applied Pareto config #%d from run %s", config_index, run_id)
            return params

    def _suggest_params(self, trial: Any) -> dict[str, Any]:
        """Use Optuna trial to suggest parameter values from search space."""
        params: dict[str, Any] = {}
        for name, spec in SEARCH_SPACE.items():
            if spec.type == "int":
                params[name] = trial.suggest_int(name, int(spec.low), int(spec.high))
            elif spec.type == "float":
                params[name] = trial.suggest_float(name, spec.low, spec.high)
        return params

    def _extract_pareto_configs(self, study: Any) -> list[ParetoConfig]:
        """Extract Pareto-optimal configurations from a completed study."""
        configs: list[ParetoConfig] = []
        for trial in study.best_trials:
            configs.append(
                ParetoConfig(
                    params=dict(trial.params),
                    metrics={
                        "faithfulness": trial.values[0],
                        "answer_relevancy": trial.values[1],
                        "latency_p95": -trial.values[2],
                    },
                    trial_number=trial.number,
                )
            )
        configs.sort(key=lambda c: c.metrics["faithfulness"], reverse=True)
        return configs

    def _select_balanced_config(self, pareto_configs: list[ParetoConfig]) -> ParetoConfig | None:
        """Select the config closest to ideal point on Pareto front.

        Uses normalized Euclidean distance to (max_quality, max_relevancy,
        min_latency).
        """
        if not pareto_configs:
            return None
        if len(pareto_configs) == 1:
            return pareto_configs[0]

        faithfulness_vals = [c.metrics["faithfulness"] for c in pareto_configs]
        relevancy_vals = [c.metrics["answer_relevancy"] for c in pareto_configs]
        latency_vals = [c.metrics["latency_p95"] for c in pareto_configs]

        def normalize(val: float, min_v: float, max_v: float) -> float:
            if max_v == min_v:
                return 0.5
            return (val - min_v) / (max_v - min_v)

        best_distance = float("inf")
        best_config = pareto_configs[0]

        for config in pareto_configs:
            f_norm = normalize(
                config.metrics["faithfulness"],
                min(faithfulness_vals),
                max(faithfulness_vals),
            )
            r_norm = normalize(
                config.metrics["answer_relevancy"],
                min(relevancy_vals),
                max(relevancy_vals),
            )
            l_norm = normalize(
                config.metrics["latency_p95"],
                min(latency_vals),
                max(latency_vals),
            )

            distance = ((1.0 - f_norm) ** 2 + (1.0 - r_norm) ** 2 + l_norm**2) ** 0.5

            if distance < best_distance:
                best_distance = distance
                best_config = config

        return best_config
