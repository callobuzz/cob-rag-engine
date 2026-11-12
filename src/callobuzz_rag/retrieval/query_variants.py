"""Multi-query generation and HyDE (Hypothetical Document Embeddings).

Generates query variants (rephrases) for broader retrieval recall and
optionally creates hypothetical answer paragraphs for HyDE-based search.
"""

import json
import logging

from callobuzz_rag.llm.base import LLMProvider

logger = logging.getLogger(__name__)

MULTI_QUERY_PROMPT = """Generate {count} different rephrasings of the following search query.
Each rephrasing should:
- Preserve the original intent and meaning
- Use different vocabulary and phrasing
- Target different ways the answer might be written in a document
- Be a complete, self-contained question or search query

Original query: {query}

Respond with ONLY this JSON (no other text):
{{"variants": ["rephrasing 1", "rephrasing 2", ...]}}"""

HYDE_PROMPT = """Write a short, factual paragraph (3-5 sentences) that would be the ideal
answer to the following question. Write it as if it were extracted from an authoritative
document. Do not hedge or say "I think" — write it as established fact.

Question: {query}

Respond with ONLY this JSON (no other text):
{{"hypothetical_answer": "your paragraph here"}}"""


class MultiQueryGenerator:
    """Generates rephrased query variants for broader retrieval.

    Different phrasings of the same question match different chunks in
    the vector space (vocabulary mismatch problem). By searching with
    multiple variants and fusing results via RRF, we improve recall.
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        count: int = 3,
    ) -> None:
        self._llm = llm_provider
        self._count = count

    async def generate(self, query: str) -> list[str]:
        """Generate rephrased variants of a query.

        Returns:
            List of rephrased query variants (up to self._count).
            Returns empty list on failure — original query is always used.
        """
        from callobuzz_rag.llm.models import LLMMessage
        from callobuzz_rag.retrieval.safety import sanitize_for_prompt_literal

        safe_query = sanitize_for_prompt_literal(query)
        prompt = MULTI_QUERY_PROMPT.format(count=self._count, query=safe_query)
        messages = [LLMMessage(role="user", content=prompt)]

        try:
            # Rephrasing a search string is not reasoning — use the cheap model.
            response = await self._llm.complete_json(messages, **self._llm.fast_call_options())
            result = json.loads(response.content)

            variants = list(result.get("variants", []))
            variants = [str(v).strip() for v in variants if str(v).strip()][: self._count]

            logger.info(
                "Generated %d query variants for: %s",
                len(variants),
                query[:80],
            )
            return variants

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("Multi-query LLM returned invalid JSON: %s", e)
            return []

        except Exception as e:
            logger.error("Multi-query generation failed: %s", e, exc_info=True)
            return []


class HyDEGenerator:
    """Generates hypothetical document embeddings (HyDE).

    Creates a hypothetical answer paragraph that is then embedded and
    used as a search vector. This bridges the gap between short queries
    and long document passages in the embedding space.

    Reference: Gao et al. (2022) — "Precise Zero-Shot Dense Retrieval
    without Relevance Labels"
    """

    def __init__(self, llm_provider: LLMProvider) -> None:
        self._llm = llm_provider

    async def generate(self, query: str) -> str | None:
        """Generate a hypothetical answer paragraph for HyDE.

        Returns:
            Hypothetical answer paragraph, or None on failure.
        """
        from callobuzz_rag.llm.models import LLMMessage
        from callobuzz_rag.retrieval.safety import sanitize_for_prompt_literal

        safe_query = sanitize_for_prompt_literal(query)
        prompt = HYDE_PROMPT.format(query=safe_query)
        messages = [LLMMessage(role="user", content=prompt)]

        try:
            # Deliberately NOT the fast model. HyDE writes a hypothetical answer
            # that is then embedded and searched with — its wording directly sets
            # retrieval quality, unlike classification and variants which only
            # pick or rephrase. Cheapening this one costs recall.
            response = await self._llm.complete_json(messages)
            result = json.loads(response.content)

            hyde_text = str(result.get("hypothetical_answer", "")).strip()
            if not hyde_text:
                logger.warning("HyDE LLM returned empty hypothetical answer")
                return None

            logger.info(
                "Generated HyDE text (%d chars) for: %s",
                len(hyde_text),
                query[:80],
            )
            return hyde_text

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("HyDE LLM returned invalid JSON: %s", e)
            return None

        except Exception as e:
            logger.error("HyDE generation failed: %s", e, exc_info=True)
            return None
