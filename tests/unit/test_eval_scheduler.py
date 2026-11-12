"""Unit tests for eval scheduling and config-change detection."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.core.config import Settings


def _make_settings(**overrides: object) -> Settings:
    """Create test Settings with required fields and no .env loading."""
    defaults = {
        "_env_file": None,
        "jina_api_key": "test",
        "llm_api_key": "test",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


class TestSettingsHash:
    """Tests for settings_hash() determinism and scope."""

    def test_same_settings_produce_same_hash(self) -> None:
        from callobuzz_rag.evaluation.scheduler import settings_hash

        s1 = _make_settings()
        s2 = _make_settings()
        assert settings_hash(s1) == settings_hash(s2)

    def test_different_top_k_produces_different_hash(self) -> None:
        from callobuzz_rag.evaluation.scheduler import settings_hash

        s1 = _make_settings()
        s2 = _make_settings(retrieval_top_k_dense=99)
        assert settings_hash(s1) != settings_hash(s2)

    def test_different_mmr_lambda_produces_different_hash(self) -> None:
        from callobuzz_rag.evaluation.scheduler import settings_hash

        s1 = _make_settings()
        s2 = _make_settings(mmr_lambda=0.1)
        assert settings_hash(s1) != settings_hash(s2)

    def test_different_rerank_model_produces_different_hash(self) -> None:
        from callobuzz_rag.evaluation.scheduler import settings_hash

        s1 = _make_settings()
        s2 = _make_settings(rerank_model="cross-encoder/ms-marco-MiniLM-L-6-v2")
        assert settings_hash(s1) != settings_hash(s2)

    def test_different_llm_model_produces_different_hash(self) -> None:
        from callobuzz_rag.evaluation.scheduler import settings_hash

        s1 = _make_settings()
        s2 = _make_settings(llm_model="openai/gpt-4o")
        assert settings_hash(s1) != settings_hash(s2)

    def test_non_retrieval_setting_does_not_change_hash(self) -> None:
        """Changing log_level or db_host must NOT change the hash."""
        from callobuzz_rag.evaluation.scheduler import settings_hash

        s1 = _make_settings()
        s2 = _make_settings(log_level="DEBUG", db_host="otherhost")
        assert settings_hash(s1) == settings_hash(s2)

    def test_hash_is_valid_sha256_hex(self) -> None:
        from callobuzz_rag.evaluation.scheduler import settings_hash

        h = settings_hash(_make_settings())
        assert len(h) == 64
        int(h, 16)  # Raises if not valid hex


class TestCheckConfigChange:
    """Tests for check_config_change() DB hash comparison."""

    @pytest.mark.asyncio
    async def test_returns_true_when_no_stored_hash(self) -> None:
        """First run — no hash in DB yet — should return True."""
        from callobuzz_rag.evaluation.scheduler import check_config_change

        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute.return_value = result_mock

        settings = _make_settings()
        changed = await check_config_change(session, settings)
        assert changed is True
        # Should have added the new hash
        session.add.assert_called_once()
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_false_when_hash_matches(self) -> None:
        """Same settings as stored — no change."""
        from callobuzz_rag.evaluation.scheduler import (
            check_config_change,
            settings_hash,
        )

        settings = _make_settings()
        current_hash = settings_hash(settings)

        config_row = MagicMock()
        config_row.value = {"hash": current_hash}

        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = config_row
        session.execute.return_value = result_mock

        changed = await check_config_change(session, settings)
        assert changed is False
        session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_true_when_hash_differs(self) -> None:
        """Different hash stored — config changed — should update."""
        from callobuzz_rag.evaluation.scheduler import check_config_change

        config_row = MagicMock()
        config_row.value = {"hash": "old_hash_that_does_not_match"}

        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = config_row
        session.execute.return_value = result_mock

        settings = _make_settings()
        changed = await check_config_change(session, settings)
        assert changed is True
        session.commit.assert_awaited_once()


class TestRunScheduledEvalTask:
    """Tests for the run_scheduled_eval Celery task."""

    @patch("callobuzz_rag.evaluation.scheduler._create_session")
    @patch("callobuzz_rag.evaluation.scheduler._create_eval_runner")
    @patch("callobuzz_rag.evaluation.scheduler.get_settings")
    def test_task_calls_runner(
        self,
        mock_settings: MagicMock,
        mock_runner: MagicMock,
        mock_session: MagicMock,
    ) -> None:
        from callobuzz_rag.evaluation.scheduler import run_scheduled_eval

        mock_settings.return_value = _make_settings()
        runner_instance = AsyncMock()
        runner_instance.run.return_value = {"faithfulness": 0.85}
        mock_runner.return_value = runner_instance
        mock_session.return_value = AsyncMock()

        # The task calls asyncio.run() internally — we patch at that level
        with patch("callobuzz_rag.evaluation.scheduler.asyncio") as mock_asyncio:
            mock_asyncio.run.return_value = {"faithfulness": 0.85}
            result = run_scheduled_eval()

        mock_asyncio.run.assert_called_once()
        assert result is not None
        assert result == {"faithfulness": 0.85}

    @patch("callobuzz_rag.evaluation.scheduler._create_session")
    @patch("callobuzz_rag.evaluation.scheduler._create_eval_runner")
    @patch("callobuzz_rag.evaluation.scheduler.get_settings")
    def test_task_returns_none_on_error(
        self,
        mock_settings: MagicMock,
        mock_runner: MagicMock,
        mock_session: MagicMock,
    ) -> None:
        from callobuzz_rag.evaluation.scheduler import run_scheduled_eval

        mock_settings.return_value = _make_settings()

        with patch("callobuzz_rag.evaluation.scheduler.asyncio") as mock_asyncio:
            mock_asyncio.run.side_effect = RuntimeError("eval failed")
            result = run_scheduled_eval()

        assert result is None


class TestCheckAndTriggerEvalTask:
    """Tests for the check_and_trigger_eval Celery task."""

    @patch("callobuzz_rag.evaluation.scheduler._create_session")
    @patch("callobuzz_rag.evaluation.scheduler.get_settings")
    def test_triggers_eval_when_config_changed(
        self,
        mock_settings: MagicMock,
        mock_session: MagicMock,
    ) -> None:
        from callobuzz_rag.evaluation.scheduler import check_and_trigger_eval

        mock_settings.return_value = _make_settings()
        mock_session.return_value = AsyncMock()

        with (
            patch("callobuzz_rag.evaluation.scheduler.asyncio") as mock_asyncio,
            patch("callobuzz_rag.worker.celery_app.celery_app") as mock_celery,
        ):
            mock_asyncio.run.return_value = True  # config changed
            check_and_trigger_eval()

        mock_celery.send_task.assert_called_once_with("callobuzz_rag.run_evaluation")

    @patch("callobuzz_rag.evaluation.scheduler.run_scheduled_eval")
    @patch("callobuzz_rag.evaluation.scheduler._create_session")
    @patch("callobuzz_rag.evaluation.scheduler.get_settings")
    def test_skips_eval_when_config_unchanged(
        self,
        mock_settings: MagicMock,
        mock_session: MagicMock,
        mock_run_eval: MagicMock,
    ) -> None:
        from callobuzz_rag.evaluation.scheduler import check_and_trigger_eval

        mock_settings.return_value = _make_settings()
        mock_session.return_value = AsyncMock()

        with patch("callobuzz_rag.evaluation.scheduler.asyncio") as mock_asyncio:
            mock_asyncio.run.return_value = False  # no change
            check_and_trigger_eval()

        mock_run_eval.delay.assert_not_called()

    @patch("callobuzz_rag.evaluation.scheduler._create_session")
    @patch("callobuzz_rag.evaluation.scheduler.get_settings")
    def test_handles_db_error_gracefully(
        self,
        mock_settings: MagicMock,
        mock_session: MagicMock,
    ) -> None:
        from callobuzz_rag.evaluation.scheduler import check_and_trigger_eval

        mock_settings.return_value = _make_settings()

        with patch("callobuzz_rag.evaluation.scheduler.asyncio") as mock_asyncio:
            mock_asyncio.run.side_effect = RuntimeError("DB down")
            # Should not raise
            check_and_trigger_eval()


class TestGoldenRuns:
    """kind="golden" sends the queued run to the golden runner, not the traffic evaluation."""

    async def test_a_golden_run_goes_to_the_golden_runner(self) -> None:
        import uuid

        from callobuzz_rag.evaluation import scheduler

        run_id = uuid.uuid4()
        engine = AsyncMock()
        runner = MagicMock()
        runner.return_value.execute = AsyncMock()
        session = AsyncMock()
        session.get.return_value = MagicMock(status="completed")
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=None)
        with (
            patch.object(scheduler, "get_settings", return_value=_make_settings()),
            patch("callobuzz_rag.db.engine.create_engine", return_value=engine),
            patch("callobuzz_rag.db.engine.create_session_factory", return_value=factory),
            patch("callobuzz_rag.evaluation.golden_runner.GoldenRunner", runner),
        ):
            out = await scheduler._run_eval_async(run_id=str(run_id), kind="golden")
        runner.return_value.execute.assert_awaited_once_with(run_id)
        engine.dispose.assert_awaited_once()
        assert out == {"eval_run_id": str(run_id), "status": "completed"}

    def test_the_task_passes_kind_through(self) -> None:
        from callobuzz_rag.worker.beat_tasks import run_evaluation_task

        with patch("callobuzz_rag.evaluation.scheduler.run_scheduled_eval") as run:
            run.return_value = {"status": "completed"}
            run_evaluation_task(kind="golden", run_id="r1")
        assert run.call_args.kwargs["kind"] == "golden"
        assert run.call_args.kwargs["run_id"] == "r1"
