"""Strategist validator — checks logical coherence of the response.

Uses an LLM to evaluate: "Is the reasoning logically consistent?
Are there contradictions or logical gaps?" Returns binary coherence
judgment with specific issues listed.
"""

import json
import logging

from callobuzz_rag.llm.base import LLMProvider
from callobuzz_rag.retrieval.validation.base import (
    ValidationResult,
    ValidatorProvider,
)

logger = logging.getLogger(__name__)

STRATEGIST_PROMPT = """You are a logical coherence evaluator. Your job is to check whether
a response is internally consistent and logically sound.

Generated response:
{response}

Check for:
- Internal contradictions (saying X in one place and not-X in another)
- Logical gaps (conclusions that don't follow from premises)
- Circular reasoning
- Unsupported generalizations or hedged non-answers

Respond with ONLY this JSON (no other text):
{{"coherent": true/false, "issues": ["issue 1 description", ...]}}

Set "coherent" to true only if the response is logically consistent throughout."""


class Strategist(ValidatorProvider):
    """Checks logical coherence and consistency of the response.

    Unlike Gatekeeper (query-response relevance) and Auditor (context
    faithfulness), the Strategist evaluates the response in isolation
    for internal logical consistency.
    """

    def __init__(self, llm_provider: LLMProvider) -> None:
        self._llm = llm_provider

    async def validate(
        self,
        query: str,
        response: str,
        context_chunks: list[str],
    ) -> ValidationResult:
        """Check response for logical coherence.

        Args:
            query: Not used by strategist (coherence is response-internal).
            response: The generated response to check.
            context_chunks: Not used by strategist.

        Returns:
            ValidationResult with score 1.0 (coherent) or 0.0 (incoherent).
        """
        from callobuzz_rag.llm.models import LLMMessage
        from callobuzz_rag.retrieval.safety import sanitize_for_prompt_literal

        safe_response = sanitize_for_prompt_literal(response)

        prompt = STRATEGIST_PROMPT.format(response=safe_response)
        messages = [LLMMessage(role="user", content=prompt)]

        try:
            llm_response = await self._llm.complete_json(messages)
            result = json.loads(llm_response.content)

            coherent = bool(result.get("coherent", False))
            issues = list(result.get("issues", []))

            return ValidationResult(
                passed=coherent,
                score=1.0 if coherent else 0.0,
                reason=(
                    "Response is logically coherent"
                    if coherent
                    else f"{len(issues)} coherence issue(s) found"
                ),
                details={"issues": issues},
            )

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("Strategist LLM returned invalid JSON: %s", e)
            return ValidationResult(
                passed=True,
                score=0.5,
                reason=f"Strategist check inconclusive: {e}",
            )

        except Exception as e:
            logger.error("Strategist validation failed: %s", e, exc_info=True)
            return ValidationResult(
                passed=True,
                score=0.5,
                reason=f"Strategist error (fail-open): {e}",
            )
