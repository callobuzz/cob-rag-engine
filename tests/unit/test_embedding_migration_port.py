"""Regressions found while porting embedding migration onto the tenant-layer engine.

1. The deployed default (TEI serving BAAI/bge-base-en-v1.5, 768 dims) was missing
   from the embedding catalog, so resolving it raised ValueError.
2. That ValueError escaped detect_drift and stopped the scan for every corpus.
3. The re-encrypt pass rotated only llm_config, leaving embedding_config's key
   under the old encryption key.
"""

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet

from callobuzz_rag.core.model_catalog import get_embedding_catalog_entry
from callobuzz_rag.core.reencrypt import _ROTATORS, RotationSummary, _corpus_key_rotator
from callobuzz_rag.core.secrets import FernetEncryptor
from callobuzz_rag.migration import service
from callobuzz_rag.migration.service import detect_drift, is_drifted


def _settings(provider: str, model: str, dimensions: int) -> MagicMock:
    settings = MagicMock()
    settings.embedding_provider = provider
    settings.embedding_model = model
    settings.embedding_dimensions = dimensions
    return settings


def _corpus(applied: dict[str, Any] | None, embedding_config: Any = None) -> MagicMock:
    corpus = MagicMock()
    corpus.id = uuid.uuid4()
    corpus.name = "c"
    corpus.qdrant_collection = f"corpus_{str(corpus.id)[:8]}"
    corpus.embedding_config = embedding_config
    corpus.applied_embedding = applied
    return corpus


class TestDeployedDefaultIsCatalogued:
    @pytest.mark.parametrize("provider", ["tei", "local"])
    def test_bge_base_resolves_to_768(self, provider: str) -> None:
        entry = get_embedding_catalog_entry("BAAI/bge-base-en-v1.5", provider=provider)
        assert entry is not None
        assert entry["dimensions"] == 768

    def test_tei_default_with_matching_backfill_is_not_drift(self) -> None:
        settings = _settings("tei", "BAAI/bge-base-en-v1.5", 768)
        corpus = _corpus({"provider": "tei", "model": "BAAI/bge-base-en-v1.5", "dimensions": 768})
        assert not is_drifted(corpus, settings)


class TestDriftScanSurvivesAnUnresolvableCorpus:
    @pytest.mark.asyncio
    async def test_bad_corpus_is_skipped_and_the_next_one_still_migrates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = _settings("tei", "BAAI/bge-base-en-v1.5", 768)
        bad = _corpus(
            {"provider": "tei", "model": "BAAI/bge-base-en-v1.5", "dimensions": 768},
            embedding_config={"provider": "tei", "model": "not-a-real-model"},
        )
        good = _corpus({"provider": "local", "model": "BAAI/bge-m3", "dimensions": 1024})

        result = MagicMock()
        result.scalars.return_value.all.return_value = [bad, good]
        session = MagicMock()
        session.execute = AsyncMock(return_value=result)

        monkeypatch.setattr(service, "get_non_terminal_migration", AsyncMock(return_value=None))
        created_for: list[uuid.UUID] = []

        async def fake_create(_s: Any, corpus: Any, _st: Any) -> MagicMock:
            created_for.append(corpus.id)
            return MagicMock()

        monkeypatch.setattr(service, "create_migration", fake_create)

        created = await detect_drift(session, settings)

        assert created_for == [good.id]
        assert len(created) == 1


class TestReencryptCoversEmbeddingKeys:
    def test_both_encrypted_columns_are_registered(self) -> None:
        names = {r.__name__ for r in _ROTATORS}
        assert names == {
            "_rotate_corpus_llm_config",
            "_rotate_corpus_embedding_config",
            "_rotate_leads",  # lead contact data (conversation C4)
            "_rotate_handoffs",  # a handoff summary (conversation C5)
            "_rotate_channel_connections",  # a WhatsApp token (TV6)
            "_rotate_channel_threads",  # a WhatsApp contact number (TV6)
            "_rotate_channel_events",  # a stored WhatsApp event (TV6)
            "_rotate_payment_settings",  # payment provider keys (P1)
            "_rotate_payment_events",  # a stored payment webhook (P1)
        }

    @pytest.mark.asyncio
    async def test_embedding_key_is_rewritten_under_the_new_key(self) -> None:
        old_key, new_key = Fernet.generate_key().decode(), Fernet.generate_key().decode()
        token = FernetEncryptor(old_key).encrypt("jina-secret")
        corpus = _corpus(None, embedding_config={"provider": "jina", "api_key_encrypted": token})

        result = MagicMock()
        result.scalars.return_value.all.return_value = [corpus]
        session = MagicMock()
        session.execute = AsyncMock(return_value=result)
        session.get = AsyncMock(return_value=corpus)
        session.commit = AsyncMock()
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=None)

        summary = RotationSummary()
        rotator = _corpus_key_rotator("embedding_config")
        await rotator(factory, FernetEncryptor(f"{new_key},{old_key}"), True, summary)

        assert summary.rotated == 1
        assert not summary.failed
        rewritten = corpus.embedding_config["api_key_encrypted"]
        assert FernetEncryptor(new_key).decrypt(rewritten) == "jina-secret"
        assert corpus.embedding_config["provider"] == "jina"
