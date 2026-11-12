"""Query classification and sub-question decomposition.

Uses LLM to analyze query complexity and break down complex queries
into independent sub-questions for parallel retrieval.
"""

import json
import logging
from typing import Any

from callobuzz_rag.llm.base import LLMProvider
from callobuzz_rag.retrieval.models import QueryComplexity

logger = logging.getLogger(__name__)


def _as_json_object(content: str) -> dict[str, Any]:
    """Parse an LLM reply, insisting it is a JSON *object*.

    `complete_json` guarantees valid JSON, not a mapping. A model that answers
    `0.5` or `["a", "b"]` parses fine and then dies on `.get` with an
    `AttributeError` — which is not in either call site's
    `(JSONDecodeError, KeyError, TypeError)` handler, so it fell through to the
    generic one and logged a full stack trace at ERROR for what is an ordinary
    bad reply. Six of those hit `rag-app` on 2026-08-11.

    Raising `TypeError` puts it back in the handler it belongs to: warn, and
    fail safe to the neutral default.
    """
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise TypeError(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed


CLASSIFICATION_PROMPT = """Classify the following user query into exactly one category:

- "simple": Direct factual question, single topic, can be answered from one passage.
- "multi_step": Requires combining information from multiple different sources or topics.
- "comparison": Comparing two or more things (features, products, approaches, etc.).
- "analytical": Requires reasoning, trend analysis, or synthesis across data.

User query: {query}

Respond with ONLY this JSON (no other text):
{{"complexity": "simple|multi_step|comparison|analytical", "reason": "brief explanation"}}"""

DECOMPOSITION_PROMPT = """Break down the following complex query into 2-5 independent
sub-questions. Each sub-question should be self-contained and answerable from a single
passage or document section.

Original query: {query}
Query type: {complexity}

Guidelines:
- Each sub-question should target a specific piece of information
- Sub-questions should collectively cover the full original query
- For comparisons, create separate questions for each entity being compared
- Keep sub-questions concise and specific

Respond with ONLY this JSON (no other text):
{{"sub_questions": ["question 1", "question 2", ...]}}"""


class QueryClassifier:
    """Classifies query complexity using LLM.

    Uses the fast model when configured to minimize latency. Falls back
    to "simple" classification on any error (fail-safe).
    """

    def __init__(self, llm_provider: LLMProvider) -> None:
        self._llm = llm_provider

    async def classify(self, query: str) -> QueryComplexity:
        """Classify query complexity.

        Returns:
            QueryComplexity enum value. Defaults to SIMPLE on error.
        """
        from callobuzz_rag.llm.models import LLMMessage
        from callobuzz_rag.retrieval.safety import sanitize_for_prompt_literal

        safe_query = sanitize_for_prompt_literal(query)
        prompt = CLASSIFICATION_PROMPT.format(query=safe_query)
        messages = [LLMMessage(role="user", content=prompt)]

        try:
            # Bucketing a query into simple/complex does not need the answer
            # model. `fast_model` is None on providers with no cheap model, which
            # complete_json reads as "use the default".
            response = await self._llm.complete_json(messages, **self._llm.fast_call_options())
            result = _as_json_object(response.content)

            complexity_str = str(result.get("complexity", "simple"))
            try:
                return QueryComplexity(complexity_str)
            except ValueError:
                logger.warning(
                    "LLM returned unknown complexity '%s', defaulting to simple",
                    complexity_str,
                )
                return QueryComplexity.SIMPLE

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("Classification LLM returned invalid JSON: %s", e)
            return QueryComplexity.SIMPLE

        except Exception as e:
            logger.error("Query classification failed: %s", e, exc_info=True)
            return QueryComplexity.SIMPLE


class SubQuestionDecomposer:
    """Decomposes complex queries into independent sub-questions.

    Only invoked for multi_step, comparison, and analytical queries.
    Simple queries skip decomposition entirely.
    """

    def __init__(self, llm_provider: LLMProvider) -> None:
        self._llm = llm_provider

    async def decompose(
        self,
        query: str,
        complexity: QueryComplexity,
    ) -> list[str]:
        """Decompose a complex query into sub-questions.

        Returns:
            List of 2-5 sub-questions. Empty list on failure.
        """
        from callobuzz_rag.llm.models import LLMMessage
        from callobuzz_rag.retrieval.safety import sanitize_for_prompt_literal

        safe_query = sanitize_for_prompt_literal(query)
        prompt = DECOMPOSITION_PROMPT.format(query=safe_query, complexity=complexity.value)
        messages = [LLMMessage(role="user", content=prompt)]

        try:
            # Splitting one question into sub-questions is query rewriting.
            response = await self._llm.complete_json(messages, **self._llm.fast_call_options())
            result = _as_json_object(response.content)

            sub_questions = list(result.get("sub_questions", []))

            # Validate: 2-5 sub-questions, all non-empty strings
            sub_questions = [str(q).strip() for q in sub_questions if str(q).strip()][:5]

            if len(sub_questions) < 2:
                logger.warning(
                    "Decomposition returned %d sub-questions (expected 2-5), using original query",
                    len(sub_questions),
                )
                return []

            return sub_questions

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("Decomposition LLM returned invalid JSON: %s", e)
            return []

        except Exception as e:
            logger.error("Query decomposition failed: %s", e, exc_info=True)
            return []
