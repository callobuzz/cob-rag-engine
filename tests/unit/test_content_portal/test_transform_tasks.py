"""Tests for transform Celery tasks (CP-06)."""

from unittest.mock import MagicMock, patch


class TestTransformContentTask:
    def test_task_is_registered(self) -> None:
        """Transform task is registered with correct name."""
        import callobuzz_rag.content_portal.tasks.transform_tasks  # noqa: F401
        from callobuzz_rag.worker.celery_app import celery_app

        assert "portal.transform_content" in celery_app.tasks

    @patch("callobuzz_rag.content_portal.tasks.transform_tasks.asyncio")
    @patch("callobuzz_rag.content_portal.tasks.transform_tasks.get_settings")
    def test_task_dispatches_pipeline(
        self, mock_settings: MagicMock, mock_asyncio: MagicMock
    ) -> None:
        """Task calls asyncio.run with the async wrapper and returns completed."""
        mock_settings.return_value = MagicMock()
        mock_asyncio.run = MagicMock(return_value=None)

        from callobuzz_rag.content_portal.tasks.transform_tasks import (
            transform_content_task,
        )

        result = transform_content_task.apply(
            args=["content-id", "corpus-id", None],
        ).result

        assert result["status"] == "completed"
        assert result["staged_content_id"] == "content-id"
        mock_asyncio.run.assert_called_once()

    @patch("callobuzz_rag.content_portal.tasks.transform_tasks.asyncio")
    @patch("callobuzz_rag.content_portal.tasks.transform_tasks.get_settings")
    def test_task_returns_failure_on_error(
        self, mock_settings: MagicMock, mock_asyncio: MagicMock
    ) -> None:
        """Task returns failure dict when pipeline raises."""
        mock_settings.return_value = MagicMock()
        mock_asyncio.run = MagicMock(side_effect=RuntimeError("boom"))

        from callobuzz_rag.content_portal.tasks.transform_tasks import (
            transform_content_task,
        )

        result = transform_content_task.apply(
            args=["content-id", "corpus-id", None],
        ).result

        assert result["status"] == "failed"
        assert "boom" in result["error"]
