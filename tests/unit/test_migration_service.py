"""Unit tests for migration service — drift rule, state machine, supersede."""

import uuid
from unittest.mock import MagicMock

import pytest

from callobuzz_rag.migration.service import (
    NON_TERMINAL_STATUSES,
    TERMINAL_STATUSES,
    _next_collection_version,
    is_drifted,
)


@pytest.fixture
def mock_settings() -> MagicMock:
    settings = MagicMock()
    settings.embedding_provider = "local"
    settings.embedding_model = "BAAI/bge-m3"
    settings.embedding_dimensions = 1024
    settings.migration_retention_days = 7
    settings.migration_batch_size = 128
    return settings


@pytest.fixture
def mock_corpus() -> MagicMock:
    corpus = MagicMock()
    corpus.id = uuid.uuid4()
    corpus.qdrant_collection = f"corpus_{str(corpus.id)[:8]}"
    corpus.embedding_config = None
    corpus.applied_embedding = {
        "provider": "local",
        "model": "BAAI/bge-m3",
        "dimensions": 1024,
    }
    return corpus


class TestDriftRule:
    def test_no_drift_when_matching(self, mock_corpus: MagicMock, mock_settings: MagicMock) -> None:
        assert not is_drifted(mock_corpus, mock_settings)

    def test_drift_on_model_change(self, mock_corpus: MagicMock, mock_settings: MagicMock) -> None:
        mock_settings.embedding_model = "jina-embeddings-v3"
        mock_settings.embedding_provider = "jina"
        assert is_drifted(mock_corpus, mock_settings)

    def test_drift_on_provider_change(
        self, mock_corpus: MagicMock, mock_settings: MagicMock
    ) -> None:
        mock_settings.embedding_provider = "tei"
        assert is_drifted(mock_corpus, mock_settings)

    def test_drift_on_dimension_change(
        self, mock_corpus: MagicMock, mock_settings: MagicMock
    ) -> None:
        """Dimensions from catalog always match if model is the same.
        Drift happens when applied has different dimensions (e.g. from an older model)."""
        mock_corpus.applied_embedding = {
            "provider": "local",
            "model": "BAAI/bge-m3",
            "dimensions": 768,
        }
        assert is_drifted(mock_corpus, mock_settings)

    def test_null_applied_is_the_engine_default(
        self, mock_corpus: MagicMock, mock_settings: MagicMock
    ) -> None:
        """NULL = built with the engine default. It used to count as drifted, which queued
        a re-embed onto the same model for every corpus created through the API."""
        mock_corpus.applied_embedding = None
        mock_corpus.embedding_config = None
        assert not is_drifted(mock_corpus, mock_settings)

    def test_null_applied_with_a_corpus_model_is_drifted(
        self, mock_corpus: MagicMock, mock_settings: MagicMock
    ) -> None:
        mock_corpus.applied_embedding = None
        mock_corpus.embedding_config = {"provider": "jina", "model": "jina-embeddings-v3"}
        assert is_drifted(mock_corpus, mock_settings)

    def test_per_corpus_config_overrides_engine_default(
        self, mock_corpus: MagicMock, mock_settings: MagicMock
    ) -> None:
        mock_corpus.embedding_config = {
            "provider": "jina",
            "model": "jina-embeddings-v3",
        }
        assert is_drifted(mock_corpus, mock_settings)

    def test_per_corpus_config_matches_applied(
        self, mock_corpus: MagicMock, mock_settings: MagicMock
    ) -> None:
        mock_corpus.embedding_config = {
            "provider": "local",
            "model": "BAAI/bge-m3",
        }
        assert not is_drifted(mock_corpus, mock_settings)


class TestCollectionVersioning:
    def test_first_version(self, mock_corpus: MagicMock) -> None:
        result = _next_collection_version(mock_corpus)
        id_short = str(mock_corpus.id)[:8]
        assert result == f"corpus_{id_short}_v1"

    def test_increment_version(self, mock_corpus: MagicMock) -> None:
        id_short = str(mock_corpus.id)[:8]
        mock_corpus.qdrant_collection = f"corpus_{id_short}_v3"
        result = _next_collection_version(mock_corpus)
        assert result == f"corpus_{id_short}_v4"


class TestStatusConstants:
    def test_terminal_statuses(self) -> None:
        assert {"completed", "rolled_back", "cancelled", "failed"} == TERMINAL_STATUSES

    def test_non_terminal_statuses(self) -> None:
        assert {
            "pending_approval",
            "queued",
            "running",
            "verifying",
            "rolling_back",
        } == NON_TERMINAL_STATUSES

    def test_no_overlap(self) -> None:
        assert not TERMINAL_STATUSES & NON_TERMINAL_STATUSES
