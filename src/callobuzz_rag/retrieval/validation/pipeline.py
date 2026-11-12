"""Validation pipeline — orchestrates all three validators.

Runs Gatekeeper, Auditor, and Strategist concurrently, aggregates
their scores into a weighted confidence value, and determines
whether the response passes validation.
"""

import asyncio
import logging
import time

from callobuzz_rag.core.exceptions import ValidationError
from callobuzz_rag.retrieval.validation.auditor import Auditor
from callobuzz_rag.retrieval.validation.base import AggregatedValidation
from callobuzz_rag.retrieval.validation.gatekeeper import Gatekeeper
from callobuzz_rag.retrieval.validation.strategist import Strategist

logger = logging.getLogger(__name__)


class ValidationPipeline:
    """Orchestrates the three-stage validation pipeline.

    All three validators run concurrently via asyncio.gather.
    Results are aggregated into a weighted confidence score.
    """

    def __init__(
        self,
        gatekeeper: Gatekeeper,
        auditor: Auditor,
        strategist: Strategist,
        gatekeeper_weight: float = 0.4,
        auditor_weight: float = 0.4,
        strategist_weight: float = 0.2,
        confidence_threshold: float = 0.7,
    ) -> None:
        self._gatekeeper = gatekeeper
        self._auditor = auditor
        self._strategist = strategist

        # Normalize weights to sum to 1.0
        total = gatekeeper_weight + auditor_weight + strategist_weight
        if total > 0:
            self._gatekeeper_weight = gatekeeper_weight / total
            self._auditor_weight = auditor_weight / total
            self._strategist_weight = strategist_weight / total
        else:
            self._gatekeeper_weight = 1 / 3
            self._auditor_weight = 1 / 3
            self._strategist_weight = 1 / 3

        self._threshold = confidence_threshold

    async def validate(
        self,
        query: str,
        response: str,
        context_chunks: list[str],
    ) -> AggregatedValidation:
        """Run all three validators and aggregate results.

        Args:
            query: The original user query.
            response: The generated response to validate.
            context_chunks: Retrieved context used for generation.

        Returns:
            AggregatedValidation with confidence score and per-validator results.

        Raises:
            ValidationError: If all validators fail catastrophically.
        """
        t0 = time.monotonic()

        try:
            gatekeeper_result, auditor_result, strategist_result = await asyncio.gather(
                self._gatekeeper.validate(query, response, context_chunks),
                self._auditor.validate(query, response, context_chunks),
                self._strategist.validate(query, response, context_chunks),
            )
        except Exception as e:
            raise ValidationError(f"Validation pipeline failed catastrophically: {e}") from e

        # Weighted confidence score
        confidence = (
            self._gatekeeper_weight * gatekeeper_result.score
            + self._auditor_weight * auditor_result.score
            + self._strategist_weight * strategist_result.score
        )

        passed = confidence >= self._threshold

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "Validation complete: confidence=%.3f passed=%s "
            "gatekeeper=%.2f auditor=%.2f strategist=%.2f elapsed_ms=%d",
            confidence,
            passed,
            gatekeeper_result.score,
            auditor_result.score,
            strategist_result.score,
            elapsed_ms,
        )

        return AggregatedValidation(
            confidence=confidence,
            passed=passed,
            gatekeeper=gatekeeper_result,
            auditor=auditor_result,
            strategist=strategist_result,
            threshold=self._threshold,
        )

    @staticmethod
    def build_uncertainty_response(
        validation: AggregatedValidation,
    ) -> str:
        """Build a user-facing uncertainty message when validation fails."""
        reasons: list[str] = []

        if not validation.gatekeeper.passed:
            reasons.append(f"Relevance: {validation.gatekeeper.reason}")
        if not validation.auditor.passed:
            reasons.append(f"Faithfulness: {validation.auditor.reason}")
            ungrounded = validation.auditor.details.get("ungrounded_claims", [])
            if ungrounded:
                reasons.append(f"Unverified claims: {', '.join(ungrounded[:3])}")
        if not validation.strategist.passed:
            reasons.append(f"Coherence: {validation.strategist.reason}")

        reason_text = "; ".join(reasons) if reasons else "Low overall confidence"

        return (
            "I found relevant information but I'm not confident enough in "
            "the accuracy of my response to present it as definitive. "
            f"Reason: {reason_text}. "
            "Please try rephrasing your question or providing more context."
        )
