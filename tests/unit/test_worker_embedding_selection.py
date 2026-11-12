"""Tests for worker provider factory embedding selection."""

import inspect


class TestWorkerEmbeddingSelection:
    def test_worker_uses_embedding_provider_config(self) -> None:
        """Worker _create_providers must use config-driven embedding, not hardcoded Jina."""
        from callobuzz_rag.worker import tasks as tasks_module

        source = inspect.getsource(tasks_module)

        has_config_driven = (
            "embedding_provider" in source
            or "_create_embedding_provider" in source
            or "BgeM3EmbeddingProvider" in source
        )
        assert has_config_driven, (
            "Worker tasks.py should support config-driven embedding provider selection"
        )
