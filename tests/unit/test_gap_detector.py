"""Unit tests for GapDetector."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.core.config import Settings
from callobuzz_rag.embeddings.base import EmbeddingResult
from callobuzz_rag.gaps.detector import GapDetector


def _make_session_factory() -> MagicMock:
    """Create a mock async session factory that works as async context manager.

    async_sessionmaker() returns an async context manager directly (not a coroutine),
    so we use MagicMock for the factory and AsyncMock for the context manager.
    """
    mock_session = AsyncMock()

    # Make session.execute return an empty result by default
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_result)

    # Make session.add a no-op
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()

    # async_sessionmaker() returns a context manager directly (not a coroutine)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_factory = MagicMock()
    mock_factory.return_value = mock_ctx

    return mock_factory


def _make_settings(**overrides: object) -> Settings:
    """Create Settings with test defaults."""
    return Settings(
        gap_min_cluster_size=overrides.get("gap_min_cluster_size", 3),
        gap_confidence_threshold=overrides.get("gap_confidence_threshold", 0.5),
        _env_file=None,
    )


def _make_query_log(query_text: str, confidence_score: float | None = None) -> MagicMock:
    """Create a mock QueryLog entry."""
    ql = MagicMock()
    ql.query_text = query_text
    ql.confidence_score = confidence_score
    return ql


class TestGapDetector:
    @pytest.mark.asyncio
    async def test_run_with_no_queries(self) -> None:
        """When no low-confidence queries exist, report completes with 0 clusters."""
        mock_factory = _make_session_factory()
        mock_embedder = AsyncMock()
        settings = _make_settings()

        detector = GapDetector(mock_factory, mock_embedder, settings)
        report = await detector.run()

        assert report.status == "completed"
        assert report.cluster_count == 0
        assert report.clusters == []

    @pytest.mark.asyncio
    async def test_run_with_too_few_queries(self) -> None:
        """When fewer queries than min_cluster_size, report completes with 0 clusters."""
        mock_factory = _make_session_factory()
        mock_session = await mock_factory.return_value.__aenter__()

        # Return 2 queries, but min_cluster_size is 3
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [
            _make_query_log("what is X?", 0.3),
            _make_query_log("what is Y?", 0.2),
        ]
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_embedder = AsyncMock()
        settings = _make_settings(gap_min_cluster_size=3)

        detector = GapDetector(mock_factory, mock_embedder, settings)
        report = await detector.run()

        assert report.status == "completed"
        assert report.cluster_count == 0
        assert report.query_count == 2

    @pytest.mark.asyncio
    @patch("callobuzz_rag.gaps.detector._run_bertopic")
    async def test_run_with_queries_and_mock_bertopic(self, mock_bertopic: MagicMock) -> None:
        """When queries exist and BERTopic succeeds, report has clusters."""
        mock_factory = _make_session_factory()
        mock_session = await mock_factory.return_value.__aenter__()

        queries = [_make_query_log(f"query {i}", 0.3) for i in range(5)]
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = queries
        mock_session.execute = AsyncMock(return_value=mock_result)

        # Mock embedder
        mock_embedder = AsyncMock()
        mock_embedder.embed_texts = AsyncMock(
            return_value=[
                EmbeddingResult(
                    vector=[0.1] * 1024,
                    model="test",
                    model_version="1.0",
                    dimensions=1024,
                )
                for _ in range(5)
            ]
        )

        # Mock BERTopic results
        mock_bertopic.return_value = [
            {
                "topic_id": 0,
                "keywords": ["python", "code"],
                "query_count": 3,
                "representative_queries": ["query 0", "query 1", "query 2"],
            },
            {
                "topic_id": 1,
                "keywords": ["data", "analysis"],
                "query_count": 2,
                "representative_queries": ["query 3", "query 4"],
            },
        ]

        settings = _make_settings(gap_min_cluster_size=3)
        detector = GapDetector(mock_factory, mock_embedder, settings)
        report = await detector.run()

        assert report.status == "completed"
        assert report.cluster_count == 2
        assert len(report.clusters) == 2
        assert report.clusters[0]["topic_id"] == 0
        assert report.clusters[1]["keywords"] == ["data", "analysis"]

    @pytest.mark.asyncio
    @patch("callobuzz_rag.gaps.detector._run_bertopic")
    async def test_run_handles_bertopic_error(self, mock_bertopic: MagicMock) -> None:
        """When BERTopic raises, report status is failed with error message."""
        mock_factory = _make_session_factory()
        mock_session = await mock_factory.return_value.__aenter__()

        queries = [_make_query_log(f"query {i}", 0.3) for i in range(5)]
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = queries
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_embedder = AsyncMock()
        mock_embedder.embed_texts = AsyncMock(
            return_value=[
                EmbeddingResult(
                    vector=[0.1] * 1024,
                    model="test",
                    model_version="1.0",
                    dimensions=1024,
                )
                for _ in range(5)
            ]
        )

        mock_bertopic.side_effect = RuntimeError("HDBSCAN failed")

        settings = _make_settings(gap_min_cluster_size=3)
        detector = GapDetector(mock_factory, mock_embedder, settings)
        report = await detector.run()

        assert report.status == "failed"
        assert "HDBSCAN failed" in report.error_message

    @pytest.mark.asyncio
    async def test_run_handles_embedding_error(self) -> None:
        """When embed_texts raises, report status is failed with error message."""
        mock_factory = _make_session_factory()
        mock_session = await mock_factory.return_value.__aenter__()

        queries = [_make_query_log(f"query {i}", 0.3) for i in range(5)]
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = queries
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_embedder = AsyncMock()
        mock_embedder.embed_texts = AsyncMock(side_effect=RuntimeError("Connection refused"))

        settings = _make_settings(gap_min_cluster_size=3)
        detector = GapDetector(mock_factory, mock_embedder, settings)
        report = await detector.run()

        assert report.status == "failed"
        assert "Connection refused" in report.error_message
