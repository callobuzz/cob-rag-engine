"""Unit tests for query classification and sub-question decomposition."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.llm.models import LLMResponse, LLMUsage
from callobuzz_rag.retrieval.models import QueryComplexity, QueryPlan
from callobuzz_rag.retrieval.query_understanding import (
    QueryClassifier,
    SubQuestionDecomposer,
)


def _make_llm_response(content: str) -> LLMResponse:
    return LLMResponse(
        content=content,
        model="test-model",
        usage=LLMUsage(),
    )


class TestQueryComplexity:
    def test_is_str_enum(self) -> None:
        assert QueryComplexity.SIMPLE == "simple"
        assert QueryComplexity.MULTI_STEP == "multi_step"
        assert QueryComplexity.COMPARISON == "comparison"
        assert QueryComplexity.ANALYTICAL == "analytical"

    def test_from_string(self) -> None:
        assert QueryComplexity("simple") == QueryComplexity.SIMPLE
        assert QueryComplexity("multi_step") == QueryComplexity.MULTI_STEP

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            QueryComplexity("invalid")


class TestQueryPlan:
    def test_all_queries_deduplicates(self) -> None:
        plan = QueryPlan(
            complexity=QueryComplexity.SIMPLE,
            original_query="What is X?",
            variants=["What is X?", "Tell me about X"],
        )
        queries = plan.all_queries
        # "What is X?" should appear only once (case-insensitive dedup)
        assert len(queries) == 2
        assert "What is X?" in queries
        assert "Tell me about X" in queries

    def test_all_queries_includes_sub_queries(self) -> None:
        plan = QueryPlan(
            complexity=QueryComplexity.MULTI_STEP,
            original_query="Compare A and B",
            sub_queries=["What is A?", "What is B?"],
            variants=["How do A and B differ?"],
        )
        queries = plan.all_queries
        assert len(queries) == 4

    def test_all_queries_strips_whitespace(self) -> None:
        plan = QueryPlan(
            complexity=QueryComplexity.SIMPLE,
            original_query="  What is X?  ",
        )
        queries = plan.all_queries
        assert queries == ["What is X?"]

    def test_all_queries_filters_empty(self) -> None:
        plan = QueryPlan(
            complexity=QueryComplexity.SIMPLE,
            original_query="What is X?",
            variants=["", "  "],
        )
        assert plan.all_queries == ["What is X?"]

    def test_needs_decomposition_simple(self) -> None:
        plan = QueryPlan(
            complexity=QueryComplexity.SIMPLE,
            original_query="What is X?",
        )
        assert plan.needs_decomposition is False

    def test_needs_decomposition_multi_step(self) -> None:
        plan = QueryPlan(
            complexity=QueryComplexity.MULTI_STEP,
            original_query="How does X differ from Y?",
        )
        assert plan.needs_decomposition is True

    def test_needs_decomposition_comparison(self) -> None:
        plan = QueryPlan(
            complexity=QueryComplexity.COMPARISON,
            original_query="Compare A vs B",
        )
        assert plan.needs_decomposition is True

    def test_needs_decomposition_analytical(self) -> None:
        plan = QueryPlan(
            complexity=QueryComplexity.ANALYTICAL,
            original_query="What trends?",
        )
        assert plan.needs_decomposition is True


class TestQueryClassifier:
    @pytest.mark.asyncio
    async def test_classify_simple(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(
            return_value=_make_llm_response(
                json.dumps({"complexity": "simple", "reason": "direct factual"})
            )
        )

        classifier = QueryClassifier(mock_llm)
        result = await classifier.classify("What is the refund policy?")
        assert result == QueryComplexity.SIMPLE

    @pytest.mark.asyncio
    async def test_classify_comparison(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(
            return_value=_make_llm_response(
                json.dumps({"complexity": "comparison", "reason": "comparing"})
            )
        )

        classifier = QueryClassifier(mock_llm)
        result = await classifier.classify("Compare A vs B")
        assert result == QueryComplexity.COMPARISON

    @pytest.mark.asyncio
    async def test_classify_unknown_defaults_simple(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(
            return_value=_make_llm_response(
                json.dumps({"complexity": "unknown_type", "reason": "???"})
            )
        )

        classifier = QueryClassifier(mock_llm)
        result = await classifier.classify("Something")
        assert result == QueryComplexity.SIMPLE

    @pytest.mark.asyncio
    async def test_classify_invalid_json_defaults_simple(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(return_value=_make_llm_response("not valid json"))

        classifier = QueryClassifier(mock_llm)
        result = await classifier.classify("Something")
        assert result == QueryComplexity.SIMPLE

    @pytest.mark.asyncio
    async def test_classify_exception_defaults_simple(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(side_effect=RuntimeError("LLM down"))

        classifier = QueryClassifier(mock_llm)
        result = await classifier.classify("Something")
        assert result == QueryComplexity.SIMPLE

    @pytest.mark.asyncio
    @pytest.mark.parametrize("payload", ["0.5", '["simple"]', '"simple"', "null"])
    async def test_classify_non_object_json_warns_without_traceback(
        self, payload: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Valid JSON that is not an object is a bad reply, not a crash.

        `complete_json` promises valid JSON, not a mapping. `result.get(...)` on
        a float raises `AttributeError`, which is absent from the
        `(JSONDecodeError, KeyError, TypeError)` handler — so it fell to the
        generic one and logged ERROR with a full stack trace. Six of those in
        `rag-app` on 2026-08-11.

        Returning SIMPLE was already true via that generic handler, so asserting
        only the return value would pass before the fix as well. The log level
        is what actually changed.
        """
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(return_value=_make_llm_response(payload))

        classifier = QueryClassifier(mock_llm)
        with caplog.at_level(logging.DEBUG):
            result = await classifier.classify("Something")

        assert result == QueryComplexity.SIMPLE
        assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
        assert any("invalid JSON" in r.getMessage() for r in caplog.records)


class TestSubQuestionDecomposer:
    @pytest.mark.asyncio
    async def test_decompose_success(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(
            return_value=_make_llm_response(
                json.dumps(
                    {
                        "sub_questions": [
                            "What is A?",
                            "What is B?",
                            "How do they compare?",
                        ]
                    }
                )
            )
        )

        decomposer = SubQuestionDecomposer(mock_llm)
        result = await decomposer.decompose("Compare A and B", QueryComplexity.COMPARISON)
        assert len(result) == 3
        assert "What is A?" in result

    @pytest.mark.asyncio
    async def test_decompose_truncates_to_5(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(
            return_value=_make_llm_response(
                json.dumps({"sub_questions": [f"Q{i}" for i in range(10)]})
            )
        )

        decomposer = SubQuestionDecomposer(mock_llm)
        result = await decomposer.decompose("Query", QueryComplexity.ANALYTICAL)
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_decompose_fewer_than_2_returns_empty(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(
            return_value=_make_llm_response(json.dumps({"sub_questions": ["Only one"]}))
        )

        decomposer = SubQuestionDecomposer(mock_llm)
        result = await decomposer.decompose("Query", QueryComplexity.MULTI_STEP)
        assert result == []

    @pytest.mark.asyncio
    async def test_decompose_filters_empty_strings(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(
            return_value=_make_llm_response(json.dumps({"sub_questions": ["Q1", "", "  ", "Q2"]}))
        )

        decomposer = SubQuestionDecomposer(mock_llm)
        result = await decomposer.decompose("Query", QueryComplexity.MULTI_STEP)
        assert result == ["Q1", "Q2"]

    @pytest.mark.asyncio
    async def test_decompose_invalid_json_returns_empty(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(return_value=_make_llm_response("not json"))

        decomposer = SubQuestionDecomposer(mock_llm)
        result = await decomposer.decompose("Query", QueryComplexity.MULTI_STEP)
        assert result == []

    @pytest.mark.asyncio
    async def test_decompose_exception_returns_empty(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(side_effect=RuntimeError("boom"))

        decomposer = SubQuestionDecomposer(mock_llm)
        result = await decomposer.decompose("Query", QueryComplexity.MULTI_STEP)
        assert result == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("payload", ["0.5", '["a", "b"]', "null"])
    async def test_decompose_non_object_json_warns_without_traceback(
        self, payload: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The same `.get`-on-a-non-mapping hazard as the classifier's."""
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(return_value=_make_llm_response(payload))

        decomposer = SubQuestionDecomposer(mock_llm)
        with caplog.at_level(logging.DEBUG):
            result = await decomposer.decompose("Query", QueryComplexity.MULTI_STEP)

        assert result == []
        assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
        assert any("invalid JSON" in r.getMessage() for r in caplog.records)
