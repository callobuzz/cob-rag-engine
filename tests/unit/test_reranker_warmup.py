"""The cross-encoder is warmed at start when any knowledge base will use it."""

import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.core.config import Settings
from callobuzz_rag.retrieval.reranking.warmup import (
    corpus_uses_local_reranker,
    should_warm_reranker,
    warm_reranker,
)


def _settings(**over: Any) -> Settings:
    return Settings(_env_file=None, **over)


def _factory(configs: list[dict[str, Any] | None], *, fails: bool = False) -> Any:
    """A session factory whose `select(Corpus.retrieval_config)` yields `configs`."""

    @asynccontextmanager
    async def session() -> Any:
        if fails:
            raise ConnectionError("database not up")
        result = SimpleNamespace(scalars=lambda: iter(configs))
        yield SimpleNamespace(execute=AsyncMock(return_value=result))

    return session


class TestCorpusUsesLocalReranker:
    def test_enabled_on_the_corpus(self) -> None:
        assert corpus_uses_local_reranker({"reranking_enabled": True}, _settings())

    def test_off_on_the_corpus_beats_an_engine_default_of_on(self) -> None:
        settings = _settings(reranking_enabled=True)
        assert not corpus_uses_local_reranker({"reranking_enabled": False}, settings)

    def test_unset_follows_the_engine(self) -> None:
        assert corpus_uses_local_reranker({}, _settings(reranking_enabled=True))
        assert not corpus_uses_local_reranker(None, _settings())

    def test_the_api_reranker_is_not_the_local_model(self) -> None:
        config = {"reranking_enabled": True, "rerank_provider": "llm"}
        assert not corpus_uses_local_reranker(config, _settings())

    def test_the_engine_provider_applies_when_the_corpus_sets_none(self) -> None:
        assert not corpus_uses_local_reranker(
            {"reranking_enabled": True}, _settings(rerank_provider="llm")
        )


class TestShouldWarm:
    async def test_explicit_setting_wins_both_ways(self) -> None:
        assert await should_warm_reranker(_settings(rerank_warmup_enabled=True), _factory([]))
        assert not await should_warm_reranker(
            _settings(rerank_warmup_enabled=False, reranking_enabled=True),
            _factory([{"reranking_enabled": True}]),
        )

    async def test_engine_wide_reranking_warms_without_asking_the_database(self) -> None:
        assert await should_warm_reranker(
            _settings(reranking_enabled=True), _factory([], fails=True)
        )

    async def test_one_knowledge_base_is_enough(self) -> None:
        configs: list[dict[str, Any] | None] = [None, {"reranking_enabled": False}]
        configs.append({"reranking_enabled": True, "top_k": 20})
        assert await should_warm_reranker(_settings(), _factory(configs))

    async def test_none_use_it(self) -> None:
        configs: list[dict[str, Any] | None] = [None, {}, {"reranking_enabled": False}]
        assert not await should_warm_reranker(_settings(), _factory(configs))

    async def test_only_api_rerankers_means_no_warmup(self) -> None:
        configs: list[dict[str, Any] | None] = [
            {"reranking_enabled": True, "rerank_provider": "llm"}
        ]
        assert not await should_warm_reranker(_settings(), _factory(configs))

    async def test_a_database_error_means_no_warmup_not_a_crash(self) -> None:
        assert not await should_warm_reranker(_settings(), _factory([], fails=True))


class TestWarmTask:
    async def test_loads_and_logs_when_used(self, caplog: pytest.LogCaptureFixture) -> None:
        reranker = MagicMock()
        reranker.warmup = AsyncMock(return_value=1.5)
        with caplog.at_level(logging.INFO):
            done = await warm_reranker(
                reranker, _settings(), _factory([{"reranking_enabled": True}])
            )
        assert done is True
        reranker.warmup.assert_awaited_once()
        assert "warmed up in 1.5s" in caplog.text

    async def test_does_not_load_when_unused(self, caplog: pytest.LogCaptureFixture) -> None:
        reranker = MagicMock()
        reranker.warmup = AsyncMock()
        with caplog.at_level(logging.INFO):
            done = await warm_reranker(reranker, _settings(), _factory([None]))
        assert done is False
        reranker.warmup.assert_not_awaited()
        assert "not warmed" in caplog.text

    async def test_a_failing_load_is_logged_and_swallowed(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        reranker = MagicMock()
        reranker.warmup = AsyncMock(side_effect=RuntimeError("out of memory"))
        with caplog.at_level(logging.WARNING):
            done = await warm_reranker(reranker, _settings(reranking_enabled=True), _factory([]))
        assert done is False
        assert "first query will be slow" in caplog.text
