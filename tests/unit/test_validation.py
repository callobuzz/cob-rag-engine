"""Unit tests for the three-stage validation pipeline."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.core.exceptions import ValidationError
from callobuzz_rag.llm.models import LLMResponse, LLMUsage
from callobuzz_rag.retrieval.validation.auditor import Auditor
from callobuzz_rag.retrieval.validation.base import (
    AggregatedValidation,
    ValidationResult,
    ValidatorProvider,
)
from callobuzz_rag.retrieval.validation.gatekeeper import Gatekeeper
from callobuzz_rag.retrieval.validation.pipeline import ValidationPipeline
from callobuzz_rag.retrieval.validation.strategist import Strategist


def _make_llm_response(content: str) -> LLMResponse:
    return LLMResponse(
        content=content,
        model="test-model",
        usage=LLMUsage(),
    )


class TestValidationResult:
    def test_fields(self) -> None:
        r = ValidationResult(passed=True, score=0.9, reason="Good")
        assert r.passed is True
        assert r.score == 0.9
        assert r.reason == "Good"
        assert r.details == {}

    def test_with_details(self) -> None:
        r = ValidationResult(
            passed=False,
            score=0.3,
            reason="Bad",
            details={"issues": ["contradiction"]},
        )
        assert r.details["issues"] == ["contradiction"]


class TestValidatorProviderABC:
    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            ValidatorProvider()  # type: ignore[abstract]


class TestGatekeeper:
    @pytest.mark.asyncio
    async def test_answers_query_true(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(
            return_value=_make_llm_response(
                json.dumps({"answers_query": True, "reason": "Directly answers"})
            )
        )

        gk = Gatekeeper(mock_llm)
        result = await gk.validate("Q?", "Answer.", [])
        assert result.passed is True
        assert result.score == 1.0

    @pytest.mark.asyncio
    async def test_answers_query_false(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(
            return_value=_make_llm_response(
                json.dumps({"answers_query": False, "reason": "Off topic"})
            )
        )

        gk = Gatekeeper(mock_llm)
        result = await gk.validate("Q?", "Irrelevant.", [])
        assert result.passed is False
        assert result.score == 0.0

    @pytest.mark.asyncio
    async def test_invalid_json_fails_open(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(return_value=_make_llm_response("not json"))

        gk = Gatekeeper(mock_llm)
        result = await gk.validate("Q?", "Answer.", [])
        assert result.passed is True
        assert result.score == 0.5

    @pytest.mark.asyncio
    async def test_exception_fails_open(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(side_effect=RuntimeError("LLM down"))

        gk = Gatekeeper(mock_llm)
        result = await gk.validate("Q?", "Answer.", [])
        assert result.passed is True
        assert result.score == 0.5


class TestAuditor:
    @pytest.mark.asyncio
    async def test_all_grounded(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(
            return_value=_make_llm_response(json.dumps({"grounded": True, "ungrounded_claims": []}))
        )

        auditor = Auditor(mock_llm)
        result = await auditor.validate("Q?", "Grounded answer.", ["Context chunk"])
        assert result.passed is True
        assert result.score == 1.0

    @pytest.mark.asyncio
    async def test_some_ungrounded(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(
            return_value=_make_llm_response(
                json.dumps(
                    {
                        "grounded": False,
                        "ungrounded_claims": ["Claim A not in context"],
                    }
                )
            )
        )

        auditor = Auditor(mock_llm)
        result = await auditor.validate("Q?", "Mixed answer.", ["Context"])
        assert result.passed is False
        assert 0.0 < result.score < 1.0
        assert "ungrounded_claims" in result.details

    @pytest.mark.asyncio
    async def test_empty_context_returns_zero(self) -> None:
        mock_llm = MagicMock()
        auditor = Auditor(mock_llm)
        result = await auditor.validate("Q?", "Answer.", [])
        assert result.passed is False
        assert result.score == 0.0

    @pytest.mark.asyncio
    async def test_invalid_json_fails_open(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(return_value=_make_llm_response("not json"))

        auditor = Auditor(mock_llm)
        result = await auditor.validate("Q?", "Answer.", ["Context"])
        assert result.passed is True
        assert result.score == 0.5


class TestStrategist:
    @pytest.mark.asyncio
    async def test_coherent(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(
            return_value=_make_llm_response(json.dumps({"coherent": True, "issues": []}))
        )

        strat = Strategist(mock_llm)
        result = await strat.validate("Q?", "Coherent answer.", [])
        assert result.passed is True
        assert result.score == 1.0

    @pytest.mark.asyncio
    async def test_incoherent(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(
            return_value=_make_llm_response(
                json.dumps(
                    {
                        "coherent": False,
                        "issues": ["Self-contradictory"],
                    }
                )
            )
        )

        strat = Strategist(mock_llm)
        result = await strat.validate("Q?", "Bad answer.", [])
        assert result.passed is False
        assert result.score == 0.0
        assert "issues" in result.details

    @pytest.mark.asyncio
    async def test_invalid_json_fails_open(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(return_value=_make_llm_response("not json"))

        strat = Strategist(mock_llm)
        result = await strat.validate("Q?", "Answer.", [])
        assert result.passed is True
        assert result.score == 0.5


class TestValidationPipeline:
    @pytest.mark.asyncio
    async def test_all_pass_high_confidence(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(
            return_value=_make_llm_response(json.dumps({"answers_query": True, "reason": "Good"}))
        )

        # We need different responses for each validator
        gk = Gatekeeper(mock_llm)
        auditor_llm = MagicMock()
        auditor_llm.complete_json = AsyncMock(
            return_value=_make_llm_response(json.dumps({"grounded": True, "ungrounded_claims": []}))
        )
        auditor = Auditor(auditor_llm)

        strat_llm = MagicMock()
        strat_llm.complete_json = AsyncMock(
            return_value=_make_llm_response(json.dumps({"coherent": True, "issues": []}))
        )
        strategist = Strategist(strat_llm)

        pipeline = ValidationPipeline(
            gatekeeper=gk,
            auditor=auditor,
            strategist=strategist,
            gatekeeper_weight=0.4,
            auditor_weight=0.4,
            strategist_weight=0.2,
            confidence_threshold=0.7,
        )

        result = await pipeline.validate("Q?", "Answer.", ["Context"])
        assert result.passed is True
        assert result.confidence == 1.0

    @pytest.mark.asyncio
    async def test_all_fail_low_confidence(self) -> None:
        gk_llm = MagicMock()
        gk_llm.complete_json = AsyncMock(
            return_value=_make_llm_response(json.dumps({"answers_query": False, "reason": "Nope"}))
        )
        gk = Gatekeeper(gk_llm)

        aud_llm = MagicMock()
        aud_llm.complete_json = AsyncMock(
            return_value=_make_llm_response(
                json.dumps(
                    {
                        "grounded": False,
                        "ungrounded_claims": ["everything"],
                    }
                )
            )
        )
        auditor = Auditor(aud_llm)

        strat_llm = MagicMock()
        strat_llm.complete_json = AsyncMock(
            return_value=_make_llm_response(
                json.dumps({"coherent": False, "issues": ["bad logic"]})
            )
        )
        strategist = Strategist(strat_llm)

        pipeline = ValidationPipeline(
            gatekeeper=gk,
            auditor=auditor,
            strategist=strategist,
            confidence_threshold=0.7,
        )

        result = await pipeline.validate("Q?", "Bad answer.", ["Context"])
        assert result.passed is False
        assert result.confidence < 0.7

    @pytest.mark.asyncio
    async def test_weighted_confidence_calculation(self) -> None:
        gk_llm = MagicMock()
        gk_llm.complete_json = AsyncMock(
            return_value=_make_llm_response(json.dumps({"answers_query": True, "reason": "Yes"}))
        )
        gk = Gatekeeper(gk_llm)  # score = 1.0

        aud_llm = MagicMock()
        aud_llm.complete_json = AsyncMock(
            return_value=_make_llm_response(json.dumps({"grounded": True, "ungrounded_claims": []}))
        )
        auditor = Auditor(aud_llm)  # score = 1.0

        strat_llm = MagicMock()
        strat_llm.complete_json = AsyncMock(
            return_value=_make_llm_response(
                json.dumps({"coherent": False, "issues": ["contradiction"]})
            )
        )
        strategist = Strategist(strat_llm)  # score = 0.0

        pipeline = ValidationPipeline(
            gatekeeper=gk,
            auditor=auditor,
            strategist=strategist,
            gatekeeper_weight=0.4,
            auditor_weight=0.4,
            strategist_weight=0.2,
            confidence_threshold=0.7,
        )

        result = await pipeline.validate("Q?", "Answer.", ["Context"])
        # 0.4*1.0 + 0.4*1.0 + 0.2*0.0 = 0.8
        assert result.confidence == pytest.approx(0.8)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_catastrophic_failure_raises(self) -> None:
        gk = MagicMock(spec=Gatekeeper)
        gk.validate = AsyncMock(side_effect=RuntimeError("boom"))

        auditor = MagicMock(spec=Auditor)
        auditor.validate = AsyncMock(return_value=ValidationResult(passed=True, score=1.0))
        strategist = MagicMock(spec=Strategist)
        strategist.validate = AsyncMock(return_value=ValidationResult(passed=True, score=1.0))

        pipeline = ValidationPipeline(
            gatekeeper=gk,
            auditor=auditor,
            strategist=strategist,
        )

        with pytest.raises(ValidationError):
            await pipeline.validate("Q?", "Answer.", ["Context"])

    def test_weights_not_summing_to_one_normalized(self) -> None:
        """Weights 1.0, 1.0, 1.0 normalized to 1/3 each."""
        pipeline = ValidationPipeline(
            gatekeeper=MagicMock(spec=Gatekeeper),
            auditor=MagicMock(spec=Auditor),
            strategist=MagicMock(spec=Strategist),
            gatekeeper_weight=1.0,
            auditor_weight=1.0,
            strategist_weight=1.0,
        )
        assert pipeline._gatekeeper_weight == pytest.approx(1 / 3)
        assert pipeline._auditor_weight == pytest.approx(1 / 3)
        assert pipeline._strategist_weight == pytest.approx(1 / 3)

    def test_zero_weights_fallback(self) -> None:
        """All weights 0 -> equal 1/3 each."""
        pipeline = ValidationPipeline(
            gatekeeper=MagicMock(spec=Gatekeeper),
            auditor=MagicMock(spec=Auditor),
            strategist=MagicMock(spec=Strategist),
            gatekeeper_weight=0.0,
            auditor_weight=0.0,
            strategist_weight=0.0,
        )
        assert pipeline._gatekeeper_weight == pytest.approx(1 / 3)
        assert pipeline._auditor_weight == pytest.approx(1 / 3)
        assert pipeline._strategist_weight == pytest.approx(1 / 3)

    def test_build_uncertainty_response(self) -> None:
        validation = AggregatedValidation(
            confidence=0.3,
            passed=False,
            gatekeeper=ValidationResult(passed=False, score=0.0, reason="Off topic"),
            auditor=ValidationResult(
                passed=False,
                score=0.5,
                reason="2 claims ungrounded",
                details={"ungrounded_claims": ["Claim A", "Claim B"]},
            ),
            strategist=ValidationResult(passed=True, score=1.0, reason="Coherent"),
            threshold=0.7,
        )

        msg = ValidationPipeline.build_uncertainty_response(validation)
        assert "not confident" in msg
        assert "Off topic" in msg
        assert "Claim A" in msg
