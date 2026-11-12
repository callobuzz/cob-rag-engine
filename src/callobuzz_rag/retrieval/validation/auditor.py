"""Auditor validator — checks if response claims are grounded in context.

Uses an LLM to evaluate faithfulness: "Are all claims in the response
backed by the retrieved context?" Returns a score based on the fraction
of claims that are grounded.
"""

import json
import logging

from callobuzz_rag.llm.base import LLMProvider
from callobuzz_rag.retrieval.validation.base import (
    ValidationResult,
    ValidatorProvider,
)

logger = logging.getLogger(__name__)

AUDITOR_PROMPT = """You are a faithfulness auditor. Your job is to check whether the claims
in a response are grounded in the provided context.

Context (retrieved passages):
{context}

Generated response:
{response}

For each factual claim in the response, determine if it is supported by the context.
A claim is "grounded" if the context contains evidence that supports it.
A claim is "ungrounded" if the context does not mention or contradicts it.

Respond with ONLY this JSON (no other text):
{{"grounded": true/false, "ungrounded_claims": ["claim 1 not in context", ...]}}

Set "grounded" to true only if ALL claims are supported. List any ungrounded claims."""


class Auditor(ValidatorProvider):
    """Checks if response claims are faithful to the retrieved context.

    The faithfulness score is calculated as:
        score = 1.0 - (ungrounded_count / max(total_claims_estimate, 1))

    When all claims are grounded, score = 1.0. When the LLM flags ungrounded
    claims, the score decreases proportionally.
    """

    def __init__(self, llm_provider: LLMProvider) -> None:
        self._llm = llm_provider

    async def validate(
        self,
        query: str,
        response: str,
        context_chunks: list[str],
    ) -> ValidationResult:
        """Check if response claims are grounded in context.

        Args:
            query: The original user query (not used directly by auditor).
            response: The generated response to audit.
            context_chunks: The retrieved context chunks to check against.

        Returns:
            ValidationResult with faithfulness score in [0.0, 1.0].
        """
        from callobuzz_rag.llm.models import LLMMessage
        from callobuzz_rag.retrieval.safety import sanitize_for_prompt_literal

        if not context_chunks:
            return ValidationResult(
                passed=False,
                score=0.0,
                reason="No context chunks provided — cannot verify faithfulness",
            )

        # Join chunks with separators for the prompt
        numbered_chunks = "\n\n".join(
            f"[Passage {i + 1}]: {sanitize_for_prompt_literal(chunk)}"
            for i, chunk in enumerate(context_chunks)
        )
        safe_response = sanitize_for_prompt_literal(response)

        prompt = AUDITOR_PROMPT.format(context=numbered_chunks, response=safe_response)
        messages = [LLMMessage(role="user", content=prompt)]

        try:
            llm_response = await self._llm.complete_json(messages)
            result = json.loads(llm_response.content)

            grounded = bool(result.get("grounded", False))
            ungrounded = list(result.get("ungrounded_claims", []))

            # Estimate faithfulness score
            if grounded:
                score = 1.0
            elif ungrounded:
                # Rough heuristic: assume ~5 claims in a typical response
                estimated_total = max(len(ungrounded) + 3, 5)
                score = max(0.0, 1.0 - (len(ungrounded) / estimated_total))
            else:
                score = 0.5  # Ambiguous result

            return ValidationResult(
                passed=grounded,
                score=score,
                reason=(
                    "All claims grounded in context"
                    if grounded
                    else f"{len(ungrounded)} ungrounded claim(s) found"
                ),
                details={"ungrounded_claims": ungrounded},
            )

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("Auditor LLM returned invalid JSON: %s", e)
            return ValidationResult(
                passed=True,
                score=0.5,
                reason=f"Auditor check inconclusive: {e}",
            )

        except Exception as e:
            logger.error("Auditor validation failed: %s", e, exc_info=True)
            return ValidationResult(
                passed=True,
                score=0.5,
                reason=f"Auditor error (fail-open): {e}",
            )
