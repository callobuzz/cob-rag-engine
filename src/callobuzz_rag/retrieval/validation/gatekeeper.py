"""Gatekeeper validator — checks if the response answers the query.

Uses an LLM to evaluate relevance: "Does this response actually answer
the user's question?" Returns a binary pass/fail with a reason.
"""

import json
import logging

from callobuzz_rag.llm.base import LLMProvider
from callobuzz_rag.retrieval.validation.base import (
    ValidationResult,
    ValidatorProvider,
)

logger = logging.getLogger(__name__)

GATEKEEPER_PROMPT = """You are a response quality evaluator. Your job is to determine whether
the given response actually answers the user's question.

User's question:
{query}

Generated response:
{response}

Evaluate whether the response directly addresses the question. Consider:
- Does it answer what was asked (not something tangential)?
- Does it provide a substantive answer (not just "I don't know" padded with filler)?
- Is the answer relevant to the specific question?

Respond with ONLY this JSON (no other text):
{{"answers_query": true/false, "reason": "brief explanation"}}"""


class Gatekeeper(ValidatorProvider):
    """Checks if the response actually answers the user's question.

    Uses LLMProvider.complete_json for structured output. Designed to
    use the fast model when configured to minimize latency overhead.
    """

    def __init__(self, llm_provider: LLMProvider) -> None:
        self._llm = llm_provider

    async def validate(
        self,
        query: str,
        response: str,
        context_chunks: list[str],
    ) -> ValidationResult:
        """Check if response answers the query.

        Args:
            query: The original user query.
            response: The generated response.
            context_chunks: Not used by gatekeeper (relevance is query-response).

        Returns:
            ValidationResult with score 1.0 (answers) or 0.0 (does not).
        """
        from callobuzz_rag.llm.models import LLMMessage
        from callobuzz_rag.retrieval.safety import sanitize_for_prompt_literal

        safe_query = sanitize_for_prompt_literal(query)
        safe_response = sanitize_for_prompt_literal(response)

        prompt = GATEKEEPER_PROMPT.format(query=safe_query, response=safe_response)
        messages = [LLMMessage(role="user", content=prompt)]

        try:
            llm_response = await self._llm.complete_json(messages)
            result = json.loads(llm_response.content)

            answers = bool(result.get("answers_query", False))
            reason = str(result.get("reason", ""))

            return ValidationResult(
                passed=answers,
                score=1.0 if answers else 0.0,
                reason=reason,
            )

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("Gatekeeper LLM returned invalid JSON: %s", e)
            # Fail open — assume it answers rather than block valid responses
            return ValidationResult(
                passed=True,
                score=0.5,
                reason=f"Gatekeeper check inconclusive: {e}",
            )

        except Exception as e:
            logger.error("Gatekeeper validation failed: %s", e, exc_info=True)
            return ValidationResult(
                passed=True,
                score=0.5,
                reason=f"Gatekeeper error (fail-open): {e}",
            )
