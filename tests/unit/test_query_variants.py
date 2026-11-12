"""Unit tests for multi-query generation and HyDE."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.llm.models import LLMResponse, LLMUsage
from callobuzz_rag.retrieval.query_variants import (
    HyDEGenerator,
    MultiQueryGenerator,
)


def _make_llm_response(content: str) -> LLMResponse:
    return LLMResponse(
        content=content,
        model="test-model",
        usage=LLMUsage(),
    )


class TestMultiQueryGenerator:
    @pytest.mark.asyncio
    async def test_generate_success(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(
            return_value=_make_llm_response(
                json.dumps(
                    {
                        "variants": [
                            "What is the policy?",
                            "Tell me about the policy",
                            "Policy details please",
                        ]
                    }
                )
            )
        )

        gen = MultiQueryGenerator(mock_llm, count=3)
        result = await gen.generate("What is the refund policy?")
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_generate_truncates_to_count(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(
            return_value=_make_llm_response(json.dumps({"variants": [f"V{i}" for i in range(10)]}))
        )

        gen = MultiQueryGenerator(mock_llm, count=3)
        result = await gen.generate("Query")
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_generate_filters_empty(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(
            return_value=_make_llm_response(json.dumps({"variants": ["V1", "", "  ", "V2"]}))
        )

        gen = MultiQueryGenerator(mock_llm, count=5)
        result = await gen.generate("Query")
        assert result == ["V1", "V2"]

    @pytest.mark.asyncio
    async def test_generate_invalid_json_returns_empty(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(return_value=_make_llm_response("not json"))

        gen = MultiQueryGenerator(mock_llm, count=3)
        result = await gen.generate("Query")
        assert result == []

    @pytest.mark.asyncio
    async def test_generate_exception_returns_empty(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(side_effect=RuntimeError("boom"))

        gen = MultiQueryGenerator(mock_llm, count=3)
        result = await gen.generate("Query")
        assert result == []


class TestHyDEGenerator:
    @pytest.mark.asyncio
    async def test_generate_success(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(
            return_value=_make_llm_response(
                json.dumps(
                    {
                        "hypothetical_answer": (
                            "The refund policy allows returns within 30 days of purchase."
                        )
                    }
                )
            )
        )

        gen = HyDEGenerator(mock_llm)
        result = await gen.generate("What is the refund policy?")
        assert result is not None
        assert "refund" in result

    @pytest.mark.asyncio
    async def test_generate_empty_answer_returns_none(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(
            return_value=_make_llm_response(json.dumps({"hypothetical_answer": ""}))
        )

        gen = HyDEGenerator(mock_llm)
        result = await gen.generate("Query")
        assert result is None

    @pytest.mark.asyncio
    async def test_generate_invalid_json_returns_none(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(return_value=_make_llm_response("not json"))

        gen = HyDEGenerator(mock_llm)
        result = await gen.generate("Query")
        assert result is None

    @pytest.mark.asyncio
    async def test_generate_exception_returns_none(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(side_effect=RuntimeError("boom"))

        gen = HyDEGenerator(mock_llm)
        result = await gen.generate("Query")
        assert result is None
