"""Extractive enrichment provider — no LLM calls.

Phase 1 implementation:
- Summary: first 2 sentences
- Keywords: RAKE extraction
- Language: langdetect
- Confidence: heuristic based on chunk quality signals
"""

import logging
import re

from langdetect import DetectorFactory, LangDetectException, detect
from rake_nltk import Rake

from callobuzz_rag.chunking.models import ChunkResult
from callobuzz_rag.enrichment.base import EnrichmentProvider
from callobuzz_rag.enrichment.models import EnrichmentResult

logger = logging.getLogger(__name__)

# Make langdetect deterministic
DetectorFactory.seed = 0

# Sentence boundary regex: . ! ? and the Devanagari danda / double danda (Hindi, Marathi...)
SENTENCE_RE = re.compile(r"(?<=[.!?।॥])\s+")


class ExtractiveEnrichmentProvider(EnrichmentProvider):
    """Extractive metadata enrichment using lightweight NLP libraries."""

    def __init__(self, max_keywords: int = 10) -> None:
        self._max_keywords = max_keywords
        self._rake = Rake()

    async def enrich(self, chunk: ChunkResult) -> EnrichmentResult:
        text = chunk.text

        return EnrichmentResult(
            summary=self._extract_summary(text),
            keywords=self._extract_keywords(text),
            language=self._detect_language(text),
            confidence_score=self._compute_confidence(chunk),
        )

    def _extract_summary(self, text: str, max_sentences: int = 2) -> str | None:
        """Extract first N sentences as summary."""
        if not text or len(text.strip()) < 10:
            return None
        sentences = SENTENCE_RE.split(text.strip())
        summary = " ".join(sentences[:max_sentences]).strip()
        # Cap at 500 chars
        if len(summary) > 500:
            summary = summary[:497] + "..."
        return summary or None

    def _extract_keywords(self, text: str) -> list[str]:
        """Extract keywords using RAKE algorithm."""
        if not text or len(text.strip()) < 20:
            return []
        try:
            # Truncate very long text for performance
            input_text = text[:5000] if len(text) > 5000 else text
            self._rake.extract_keywords_from_text(input_text)
            phrases: list[str] = self._rake.get_ranked_phrases()
            # Take top N, filter out very long phrases
            return [p for p in phrases[: self._max_keywords] if len(p) < 100]
        except Exception:
            logger.warning("Keyword extraction failed", exc_info=True)
            return []

    def _detect_language(self, text: str) -> str | None:
        """Detect language of text."""
        if not text or len(text.strip()) < 20:
            return None
        try:
            result: str = detect(text)
            return result
        except LangDetectException:
            return None

    def _compute_confidence(self, chunk: ChunkResult) -> float:
        """Heuristic confidence score based on chunk quality signals.

        Returns value between 0.0 and 1.0.
        Factors: token count, is_table, is_code, text quality.
        """
        score = 0.7  # Base score

        # Bonus for reasonable length
        if 100 <= chunk.length_tokens <= 500:
            score += 0.15
        elif 50 <= chunk.length_tokens <= 1000:
            score += 0.05

        # Penalty for very short chunks
        if chunk.length_tokens < 30:
            score -= 0.3

        # Tables and code are usually well-structured
        if chunk.is_table or chunk.is_code:
            score += 0.1

        # Penalty for text that looks like OCR garbage
        if chunk.text:
            alnum_ratio = sum(c.isalnum() or c.isspace() for c in chunk.text) / max(
                len(chunk.text), 1
            )
            if alnum_ratio < 0.6:
                score -= 0.3

        return max(0.0, min(1.0, score))
