"""Abstract base for response validators.

Each validator evaluates one dimension of response quality.
All validators return a ValidationResult with a score and details.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result from a single validation check.

    Attributes:
        passed: Whether the check passed.
        score: Numeric score in [0.0, 1.0]. 1.0 = perfect.
        reason: Human-readable explanation of the result.
        details: Additional structured data (e.g., list of ungrounded claims).
    """

    passed: bool
    score: float
    reason: str = ""
    details: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class AggregatedValidation:
    """Aggregated result from the full validation pipeline.

    Attributes:
        confidence: Weighted average score across all validators.
        passed: Whether confidence exceeds the threshold.
        gatekeeper: Result from the gatekeeper validator.
        auditor: Result from the auditor validator.
        strategist: Result from the strategist validator.
        threshold: The confidence threshold used.
    """

    confidence: float
    passed: bool
    gatekeeper: ValidationResult
    auditor: ValidationResult
    strategist: ValidationResult
    threshold: float


class ValidatorProvider(ABC):
    """ABC for response validators.

    Each implementation checks one quality dimension (relevance,
    faithfulness, coherence) and returns a ValidationResult.
    """

    @abstractmethod
    async def validate(
        self,
        query: str,
        response: str,
        context_chunks: list[str],
    ) -> ValidationResult:
        """Validate a response against the query and/or context.

        Args:
            query: The original user query.
            response: The generated response to validate.
            context_chunks: Retrieved context chunks used for generation.

        Returns:
            ValidationResult with score and details.
        """
        ...
