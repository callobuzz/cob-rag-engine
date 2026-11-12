"""Unit tests for EvalRun model definition."""

from callobuzz_rag.db.models.eval_run import EvalRun


class TestEvalRunModel:
    def test_tablename(self) -> None:
        assert EvalRun.__tablename__ == "eval_run"

    def test_has_required_columns(self) -> None:
        """Verify all expected columns exist on the model."""
        column_names = {c.name for c in EvalRun.__table__.columns}
        expected = {
            "id",
            "triggered_by",
            "status",
            "started_at",
            "completed_at",
            "sample_size",
            "metrics",
            "config_snapshot",
            "error_message",
        }
        assert expected.issubset(column_names)

    def test_triggered_by_values(self) -> None:
        """triggered_by accepts the three expected trigger types."""
        for trigger in ("manual", "schedule", "config_change"):
            run = EvalRun()
            run.triggered_by = trigger
            assert run.triggered_by == trigger

    def test_metrics_is_jsonb(self) -> None:
        """metrics column should accept dict data."""
        run = EvalRun()
        run.metrics = {"faithfulness": 0.95, "answer_relevancy": 0.88}
        assert run.metrics["faithfulness"] == 0.95

    def test_config_snapshot_is_jsonb(self) -> None:
        """config_snapshot column should accept dict data."""
        run = EvalRun()
        run.config_snapshot = {
            "top_k": 10,
            "rerank_model": "cross-encoder/ms-marco",
        }
        assert run.config_snapshot["top_k"] == 10
