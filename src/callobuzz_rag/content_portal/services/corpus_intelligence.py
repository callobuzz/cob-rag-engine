"""Corpus Intelligence Service — cross-document analysis for Content Portal.

Provides duplicate detection, contradiction analysis, and gap/coverage
analysis by combining vector retrieval with LLM reasoning.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

from callobuzz_rag.core.exceptions import ContentPortalError
from callobuzz_rag.llm.models import LLMMessage, LLMResponse, LLMUsage

if TYPE_CHECKING:
    from callobuzz_rag.llm.base import LLMProvider
    from callobuzz_rag.retrieval.orchestrator import QueryOrchestrator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DUPLICATE_SCORE_THRESHOLD = 0.92
HIGH_SIMILARITY_THRESHOLD = 0.80
CONTRADICTION_CONTEXT_TOP_K = 5
GAP_ANALYSIS_TOP_K = 10

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CorpusIntelligenceError(ContentPortalError):
    """Corpus intelligence analysis failed."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DuplicateMatch:
    """A single duplicate match against an existing corpus chunk."""

    existing_chunk_id: str
    existing_text: str
    similarity_score: float
    is_exact_duplicate: bool
    document_id: str | None = None
    file_name: str | None = None


@dataclass(frozen=True)
class DuplicateReport:
    """Duplicate detection results for a single staged chunk."""

    staged_chunk_id: UUID
    staged_text_preview: str
    matches: list[DuplicateMatch]
    has_duplicates: bool = False
    has_near_duplicates: bool = False


@dataclass(frozen=True)
class Contradiction:
    """A detected contradiction between new and existing content."""

    existing_chunk_id: str
    existing_text: str
    new_text: str
    explanation: str
    severity: str
    confidence: float


@dataclass(frozen=True)
class ContradictionReport:
    """Contradiction detection results for a single staged chunk."""

    staged_chunk_id: UUID
    staged_text_preview: str
    contradictions: list[Contradiction]
    has_contradictions: bool = False
    token_usage: LLMUsage | None = None


@dataclass(frozen=True)
class GapTopic:
    """A topic identified as a gap in the existing corpus."""

    topic: str
    description: str
    confidence: float
    supporting_text: str


@dataclass(frozen=True)
class GapAnalysisReport:
    """Gap analysis results for a workspace's staged content."""

    workspace_id: UUID
    new_topics: list[GapTopic] = field(default_factory=list)
    covered_topics: list[str] = field(default_factory=list)
    has_new_knowledge: bool = False
    token_usage: LLMUsage | None = None


@dataclass(frozen=True)
class IntelligenceReport:
    """Full intelligence analysis report for a workspace."""

    workspace_id: UUID
    corpus_id: UUID
    duplicate_reports: list[DuplicateReport]
    contradiction_reports: list[ContradictionReport]
    gap_report: GapAnalysisReport | None
    total_duplicates: int = 0
    total_near_duplicates: int = 0
    total_contradictions: int = 0
    total_new_topics: int = 0
    total_token_usage: LLMUsage | None = None


# ---------------------------------------------------------------------------
# LLM Prompt templates
# ---------------------------------------------------------------------------

CONTRADICTION_DETECTION_PROMPT = """\
You are a knowledge base consistency analyst. Your task is to detect \
contradictions between NEW content and EXISTING content in a knowledge base.

Analyze the new content against each existing chunk and identify any \
factual contradictions, conflicting instructions, or inconsistent information.

Return a JSON object with:
- "contradictions": list of objects, each with:
  - "existing_chunk_id": the ID of the contradicting existing chunk
  - "explanation": clear description of the contradiction
  - "severity": "high" | "medium" | "low"
  - "confidence": float 0.0-1.0

If no contradictions are found, return {"contradictions": []}.
Return ONLY a JSON object, no explanation.
"""

GAP_ANALYSIS_PROMPT = """\
You are a knowledge base coverage analyst. Your task is to identify \
what NEW topics or knowledge the staged content introduces that are NOT \
already covered by the existing corpus content.

Analyze the new content against the existing content and identify:
1. Topics that are genuinely new (not covered at all)
2. Topics that are already well-covered

Return a JSON object with:
- "new_topics": list of objects, each with:
  - "topic": short topic name
  - "description": what new knowledge this adds
  - "confidence": float 0.0-1.0
  - "supporting_text": brief excerpt from the new content
- "covered_topics": list of strings naming topics already in the corpus

Return ONLY a JSON object, no explanation.
"""


# ---------------------------------------------------------------------------
# Service class
# ---------------------------------------------------------------------------


class CorpusIntelligenceService:
    """Cross-document intelligence: duplicates, contradictions, gaps."""

    def __init__(self, orchestrator: QueryOrchestrator, llm: LLMProvider) -> None:
        self._orchestrator = orchestrator
        self._llm = llm

    async def detect_duplicates(
        self,
        chunk_text: str,
        chunk_id: UUID,
        corpus_id: UUID,
        threshold: float = HIGH_SIMILARITY_THRESHOLD,
        model: str | None = None,
    ) -> DuplicateReport:
        """Detect duplicate and near-duplicate chunks in the existing corpus.

        Args:
            chunk_text: Text of the staged chunk to check.
            chunk_id: UUID of the staged chunk.
            corpus_id: Target corpus UUID.
            threshold: Minimum similarity score to consider a match.
            model: Optional LLM model override (unused, for API consistency).

        Returns:
            DuplicateReport with any matches found.
        """
        try:
            result = await self._orchestrator.run_retrieval_pipeline(
                text=chunk_text,
                corpus_id=corpus_id,
            )
        except Exception:
            logger.warning("Retrieval failed for duplicate detection on chunk %s", chunk_id)
            return DuplicateReport(
                staged_chunk_id=chunk_id,
                staged_text_preview=chunk_text[:200],
                matches=[],
            )

        matches: list[DuplicateMatch] = []
        for search_result in result.assembled.cited_chunks:
            score = search_result.score
            if score < threshold:
                continue
            matches.append(
                DuplicateMatch(
                    existing_chunk_id=str(search_result.chunk_id),
                    existing_text=search_result.text,
                    similarity_score=score,
                    is_exact_duplicate=score >= DUPLICATE_SCORE_THRESHOLD,
                    document_id=(
                        str(search_result.document_id) if search_result.document_id else None
                    ),
                    file_name=getattr(search_result, "file_name", None),
                )
            )

        has_exact = any(m.is_exact_duplicate for m in matches)
        has_near = any(not m.is_exact_duplicate for m in matches)

        return DuplicateReport(
            staged_chunk_id=chunk_id,
            staged_text_preview=chunk_text[:200],
            matches=matches,
            has_duplicates=has_exact,
            has_near_duplicates=has_near,
        )

    async def detect_contradictions(
        self,
        chunk_text: str,
        chunk_id: UUID,
        corpus_id: UUID,
        model: str | None = None,
    ) -> ContradictionReport:
        """Detect contradictions between a staged chunk and existing corpus.

        Args:
            chunk_text: Text of the staged chunk.
            chunk_id: UUID of the staged chunk.
            corpus_id: Target corpus UUID.
            model: Optional LLM model override.

        Returns:
            ContradictionReport with any contradictions found.

        Raises:
            CorpusIntelligenceError: If the LLM call fails.
        """
        try:
            result = await self._orchestrator.run_retrieval_pipeline(
                text=chunk_text,
                corpus_id=corpus_id,
                top_k=CONTRADICTION_CONTEXT_TOP_K,
            )
        except Exception:
            logger.warning("Retrieval failed for contradiction detection on chunk %s", chunk_id)
            return ContradictionReport(
                staged_chunk_id=chunk_id,
                staged_text_preview=chunk_text[:200],
                contradictions=[],
            )

        existing_results = result.assembled.cited_chunks
        if not existing_results:
            return ContradictionReport(
                staged_chunk_id=chunk_id,
                staged_text_preview=chunk_text[:200],
                contradictions=[],
            )

        # Build user message with existing content
        existing_content = "\n\n".join(
            f"[Chunk ID: {c.chunk_id}]\n{c.llm_text}" for c in existing_results
        )
        user_msg = (
            f"NEW CONTENT:\n---\n{chunk_text}\n---\n\n"
            f"EXISTING CONTENT:\n---\n{existing_content}\n---"
        )

        messages = [
            LLMMessage(role="system", content=CONTRADICTION_DETECTION_PROMPT),
            LLMMessage(role="user", content=user_msg),
        ]

        try:
            llm_response: LLMResponse = await self._llm.complete_json(messages, model=model)
        except Exception as e:
            raise CorpusIntelligenceError(
                f"LLM call failed for contradiction detection: {e}"
            ) from e

        contradictions = _parse_contradiction_response(
            llm_response.content, chunk_text, existing_results
        )

        return ContradictionReport(
            staged_chunk_id=chunk_id,
            staged_text_preview=chunk_text[:200],
            contradictions=contradictions,
            has_contradictions=len(contradictions) > 0,
            token_usage=llm_response.usage,
        )

    async def analyze_gaps(
        self,
        chunk_texts: list[str],
        workspace_id: UUID,
        corpus_id: UUID,
        model: str | None = None,
    ) -> GapAnalysisReport:
        """Analyze what new knowledge the staged content adds to the corpus.

        Args:
            chunk_texts: List of staged chunk texts.
            workspace_id: UUID of the workspace.
            corpus_id: Target corpus UUID.
            model: Optional LLM model override.

        Returns:
            GapAnalysisReport with new/covered topics.

        Raises:
            CorpusIntelligenceError: If the LLM call fails.
        """
        if not chunk_texts:
            return GapAnalysisReport(workspace_id=workspace_id)

        combined = "\n\n".join(chunk_texts)
        if len(combined) > 15000:
            combined = combined[:15000] + "\n[... truncated ...]"

        try:
            result = await self._orchestrator.run_retrieval_pipeline(
                text=combined[:1000],
                corpus_id=corpus_id,
                top_k=GAP_ANALYSIS_TOP_K,
            )
        except Exception:
            logger.warning("Retrieval failed for gap analysis on workspace %s", workspace_id)
            result = None

        existing_content = ""
        if result and result.assembled.cited_chunks:
            existing_content = "\n\n".join(
                f"[Chunk ID: {c.chunk_id}]\n{c.llm_text}" for c in result.assembled.cited_chunks
            )

        user_msg = (
            f"NEW CONTENT:\n---\n{combined}\n---\n\n"
            f"EXISTING CORPUS CONTENT:\n---\n{existing_content or '(empty corpus)'}\n---"
        )

        messages = [
            LLMMessage(role="system", content=GAP_ANALYSIS_PROMPT),
            LLMMessage(role="user", content=user_msg),
        ]

        try:
            llm_response: LLMResponse = await self._llm.complete_json(messages, model=model)
        except Exception as e:
            raise CorpusIntelligenceError(f"Gap analysis LLM call failed: {e}") from e

        return _parse_gap_response(llm_response.content, workspace_id, llm_response.usage)

    async def full_analysis(
        self,
        chunks: list[tuple[UUID, str]],
        workspace_id: UUID,
        corpus_id: UUID,
        model: str | None = None,
    ) -> IntelligenceReport:
        """Run full intelligence analysis: duplicates + contradictions + gaps.

        Args:
            chunks: List of (chunk_id, chunk_text) tuples.
            workspace_id: UUID of the workspace.
            corpus_id: Target corpus UUID.
            model: Optional LLM model override.

        Returns:
            IntelligenceReport with all analysis results.
        """
        duplicate_reports: list[DuplicateReport] = []
        contradiction_reports: list[ContradictionReport] = []

        total_prompt = 0
        total_completion = 0
        total_total = 0

        for chunk_id, chunk_text in chunks:
            dup_report = await self.detect_duplicates(chunk_text, chunk_id, corpus_id, model=model)
            duplicate_reports.append(dup_report)

            contra_report = await self.detect_contradictions(
                chunk_text, chunk_id, corpus_id, model=model
            )
            contradiction_reports.append(contra_report)

            if contra_report.token_usage:
                total_prompt += contra_report.token_usage.prompt_tokens
                total_completion += contra_report.token_usage.completion_tokens
                total_total += contra_report.token_usage.total_tokens

        chunk_texts = [text for _, text in chunks]
        gap_report = await self.analyze_gaps(chunk_texts, workspace_id, corpus_id, model=model)

        if gap_report.token_usage:
            total_prompt += gap_report.token_usage.prompt_tokens
            total_completion += gap_report.token_usage.completion_tokens
            total_total += gap_report.token_usage.total_tokens

        total_usage = LLMUsage(
            prompt_tokens=total_prompt,
            completion_tokens=total_completion,
            total_tokens=total_total,
        )

        total_duplicates = sum(
            1 for r in duplicate_reports for m in r.matches if m.is_exact_duplicate
        )
        total_near_duplicates = sum(
            1 for r in duplicate_reports for m in r.matches if not m.is_exact_duplicate
        )
        total_contradictions = sum(len(r.contradictions) for r in contradiction_reports)
        total_new_topics = len(gap_report.new_topics) if gap_report else 0

        return IntelligenceReport(
            workspace_id=workspace_id,
            corpus_id=corpus_id,
            duplicate_reports=duplicate_reports,
            contradiction_reports=contradiction_reports,
            gap_report=gap_report,
            total_duplicates=total_duplicates,
            total_near_duplicates=total_near_duplicates,
            total_contradictions=total_contradictions,
            total_new_topics=total_new_topics,
            total_token_usage=total_usage,
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _parse_contradiction_response(
    llm_content: str,
    new_text: str,
    existing_results: Sequence[object],
) -> list[Contradiction]:
    """Parse LLM JSON response into Contradiction objects.

    Args:
        llm_content: Raw JSON string from LLM.
        new_text: The new chunk text (for inclusion in each Contradiction).
        existing_results: List of existing search results (for text lookup).

    Returns:
        List of Contradiction objects. Empty list on parse failure.
    """
    try:
        data = json.loads(llm_content)
    except json.JSONDecodeError:
        logger.warning("Failed to parse contradiction response as JSON")
        return []

    contradictions: list[Contradiction] = []
    raw_list = data.get("contradictions", [])
    if not isinstance(raw_list, list):
        return []

    # Build lookup for existing chunk texts
    existing_lookup: dict[str, str] = {}
    for r in existing_results:
        chunk_id = str(getattr(r, "chunk_id", ""))
        text = getattr(r, "text", "")
        if chunk_id:
            existing_lookup[chunk_id] = text

    truncated_new = new_text[:500]

    for item in raw_list:
        if not isinstance(item, dict):
            continue

        existing_chunk_id = str(item.get("existing_chunk_id", ""))
        severity = item.get("severity", "medium")
        if severity not in ("high", "medium", "low"):
            severity = "medium"

        confidence = item.get("confidence", 0.5)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))

        existing_text = existing_lookup.get(existing_chunk_id, "")

        contradictions.append(
            Contradiction(
                existing_chunk_id=existing_chunk_id,
                existing_text=existing_text,
                new_text=truncated_new,
                explanation=str(item.get("explanation", "")),
                severity=severity,
                confidence=confidence,
            )
        )

    return contradictions


def _parse_gap_response(
    llm_content: str,
    workspace_id: UUID,
    usage: LLMUsage | None,
) -> GapAnalysisReport:
    """Parse LLM JSON response into a GapAnalysisReport.

    Args:
        llm_content: Raw JSON string from LLM.
        workspace_id: UUID of the workspace.
        usage: Token usage from the LLM call.

    Returns:
        GapAnalysisReport. Empty report on parse failure.
    """
    try:
        data = json.loads(llm_content)
    except json.JSONDecodeError:
        logger.warning("Failed to parse gap analysis response as JSON")
        return GapAnalysisReport(workspace_id=workspace_id, token_usage=usage)

    new_topics: list[GapTopic] = []
    raw_topics = data.get("new_topics", [])
    if isinstance(raw_topics, list):
        for item in raw_topics:
            if not isinstance(item, dict):
                continue
            confidence = item.get("confidence", 0.5)
            try:
                confidence = float(confidence)
            except (TypeError, ValueError):
                confidence = 0.5
            confidence = max(0.0, min(1.0, confidence))

            new_topics.append(
                GapTopic(
                    topic=str(item.get("topic", "")),
                    description=str(item.get("description", "")),
                    confidence=confidence,
                    supporting_text=str(item.get("supporting_text", "")),
                )
            )

    covered_topics = data.get("covered_topics", [])
    if not isinstance(covered_topics, list):
        covered_topics = []

    return GapAnalysisReport(
        workspace_id=workspace_id,
        new_topics=new_topics,
        covered_topics=covered_topics,
        has_new_knowledge=len(new_topics) > 0,
        token_usage=usage,
    )
