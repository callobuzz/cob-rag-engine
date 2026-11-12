"""AI Transformer Service — 6-step content transformation pipeline.

Takes raw staged content and produces structured documents + RAG-ready chunks
using LLM-powered analysis. Steps:
1. Detect content type / language / complexity
2. Extract structure into clean markdown
3. Enrich content (flag gaps, contradictions, vague sections)
4. Cross-document intelligence (stub — CP-07)
5. Prepare chunks with metadata and confidence scores
6. Generate quality report
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from callobuzz_rag.billing.byok import usable_llm_config  # P5 plans
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.exceptions import ContentPortalError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

CONTENT_TYPE_DETECTION_PROMPT = """\
Analyze the following content and return a JSON object with these fields:
- "content_type": one of "faq", "article", "knowledge_base", "policy", \
"procedure", "technical_doc", "transcript", "general"
- "language": ISO 639-1 code (e.g. "en", "ar", "fr")
- "complexity": one of "simple", "moderate", "complex"
- "recommended_output": brief suggestion for best output format

Content (truncated):
---
{content}
---

Return ONLY a JSON object, no explanation.
"""

STRUCTURE_EXTRACTION_PROMPT = """\
You are a content structuring expert. Restructure the following raw content \
into clean, well-organized markdown.

Rules:
- Use proper headings (##, ###) for sections
- Fix grammar and formatting issues but preserve meaning
- Remove duplicates and merge related sections
- Keep all factual information — do NOT invent content
- Output language: {language}
- Content type: {content_type}

Raw content:
---
{content}
---

Return ONLY the restructured markdown, no explanation.
"""

CONTENT_ENRICHMENT_PROMPT = """\
Review the following structured content and flag quality issues. \
Return a JSON object with:
- "enriched_text": the content with inline annotations like [GAP: ...], \
[VAGUE: ...], [CONTRADICTION: ...]
- "flags": list of objects, each with "type" (gap|vague|contradiction|outdated), \
"location" (section/heading), "description" (what's wrong), "severity" (low|medium|high)

Content:
---
{content}
---

Return ONLY a JSON object, no explanation.
"""

CHUNK_PREPARATION_PROMPT = """\
Split the following content into RAG-ready chunks. Each chunk should be \
self-contained and meaningful for retrieval.

Return a JSON object with:
- "chunks": list of objects, each with:
  - "text": the chunk content
  - "chunk_index": integer starting at 0
  - "metadata": object with "section", "content_type", "language", any other useful keys
  - "confidence_score": float 0.0-1.0 indicating quality/completeness
  - "warnings": list of strings (empty if none)

Guidelines:
- Target 200-500 tokens per chunk
- Preserve context — include section headers in each chunk
- Content type: {content_type}
- Language: {language}

Content:
---
{content}
---

Return ONLY a JSON object, no explanation.
"""

QUALITY_REPORT_PROMPT = """\
Generate a quality assessment report for the following AI-processed content.

Content type: {content_type}
Number of chunks: {num_chunks}
Enrichment flags: {enrichment_flags}

Return a JSON object with:
- "overall_score": float 0.0-1.0
- "completeness": float 0.0-1.0
- "clarity": float 0.0-1.0
- "structure_quality": float 0.0-1.0
- "issues": list of strings describing problems
- "recommendations": list of strings with improvement suggestions

Return ONLY a JSON object, no explanation.
"""


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class TransformError(ContentPortalError):
    """AI transformation pipeline failure."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TokenTracker:
    """Tracks token usage per pipeline step."""

    steps: dict[str, dict[str, int]] = field(default_factory=dict)

    def record(self, step_name: str, prompt_tokens: int, completion_tokens: int) -> None:
        """Record token usage for a step."""
        self.steps[step_name] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }

    def total(self) -> int:
        """Total tokens across all steps."""
        return sum(s["total_tokens"] for s in self.steps.values())

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for DB storage."""
        return {
            "steps": dict(self.steps),
            "total_tokens": self.total(),
        }


@dataclass
class TransformResult:
    """Result of the AI transformation pipeline."""

    output_type: str = ""
    structured_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    chunks: list[dict[str, Any]] = field(default_factory=list)
    quality_report: dict[str, Any] = field(default_factory=dict)
    token_usage: dict[str, Any] = field(default_factory=dict)
    enrichment_flags: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------


async def _call_llm(
    model: str,
    api_key: str,
    prompt: str,
    step_name: str,
    token_tracker: TokenTracker,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> str:
    """Call LLM via litellm and track token usage.

    Args:
        model: LiteLLM model string.
        api_key: API key for the provider.
        prompt: The prompt text.
        step_name: Pipeline step name (for tracking).
        token_tracker: Token usage tracker.
        temperature: Sampling temperature.
        max_tokens: Maximum response tokens.

    Returns:
        The LLM response text.

    Raises:
        TransformError: If the LLM call fails.
    """
    import litellm  # Deferred import

    from callobuzz_rag.core.config import get_settings

    try:
        # L5a demo: the hard daily ceiling on model calls
        from callobuzz_rag.demo import budget as demo_budget

        await demo_budget.check_model_call(None, model)
        # /L5a demo
        response = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=get_settings().llm_timeout_seconds,
        )
        content = response.choices[0].message.content or ""

        # Track tokens
        usage = getattr(response, "usage", None)
        if usage:
            token_tracker.record(
                step_name,
                prompt_tokens=getattr(usage, "prompt_tokens", 0),
                completion_tokens=getattr(usage, "completion_tokens", 0),
            )
        else:
            token_tracker.record(step_name, 0, 0)

        return content
    except Exception as e:
        raise TransformError(f"LLM call failed at step '{step_name}': {e}") from e


def _parse_json_response(response: str, step_name: str) -> dict[str, Any]:
    """Parse a JSON response from LLM, stripping markdown fences if present.

    Args:
        response: Raw LLM response text.
        step_name: Pipeline step name (for error messages).

    Returns:
        Parsed JSON dict.

    Raises:
        TransformError: If JSON parsing fails.
    """
    text = response.strip()

    # Strip markdown code fences (```json ... ``` or ``` ... ```)
    if text.startswith("```"):
        # Remove opening fence (with optional language tag)
        first_newline = text.index("\n") if "\n" in text else len(text)
        text = text[first_newline + 1 :]
        # Remove closing fence
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        result = json.loads(text)
        if not isinstance(result, dict):
            raise TransformError(
                f"Step '{step_name}': expected JSON object, got {type(result).__name__}"
            )
        return result
    except json.JSONDecodeError as e:
        raise TransformError(f"Step '{step_name}': invalid JSON response: {e}") from e


# ---------------------------------------------------------------------------
# AITransformer class
# ---------------------------------------------------------------------------


class AITransformer:
    """6-step AI content transformation pipeline."""

    def __init__(
        self,
        model: str,
        api_key: str,
        settings: Settings,
        max_tokens_per_step: int = 4096,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._settings = settings
        self._max_tokens = max_tokens_per_step

    async def transform(self, content: str) -> TransformResult:
        """Run the full 6-step transformation pipeline.

        Args:
            content: Raw content text to transform.

        Returns:
            TransformResult with all outputs.

        Raises:
            TransformError: If any step fails.
        """
        tracker = TokenTracker()

        # Step 1: Detect content type
        detection = await self._step_detect_content_type(content, tracker)
        content_type = detection.get("content_type", "general")
        language = detection.get("language", "en")

        # Step 2: Extract structure
        structured_text = await self._step_extract_structure(
            content, content_type, language, tracker
        )

        # Step 3: Enrich content
        enrichment = await self._step_enrich_content(structured_text, tracker)
        enriched_text = enrichment.get("enriched_text", structured_text)
        enrichment_flags = enrichment.get("flags", [])

        # Step 4: Cross-document intelligence (stub)
        cross_doc = await self._step_cross_document_intelligence(enriched_text)

        # Step 5: Prepare chunks
        chunk_data = await self._step_prepare_chunks(enriched_text, content_type, language, tracker)
        chunks = chunk_data.get("chunks", [])

        # Step 6: Quality report
        quality_report = await self._step_generate_quality_report(
            content_type, chunks, enrichment_flags, tracker
        )

        return TransformResult(
            output_type=content_type,
            structured_text=enriched_text,
            metadata={
                "language": language,
                "complexity": detection.get("complexity", "moderate"),
                "recommended_output": detection.get("recommended_output", ""),
                "cross_document_refs": cross_doc,
            },
            chunks=chunks,
            quality_report=quality_report,
            token_usage=tracker.to_dict(),
            enrichment_flags=enrichment_flags,
        )

    async def _step_detect_content_type(
        self, content: str, tracker: TokenTracker
    ) -> dict[str, Any]:
        """Step 1: Classify content type, language, complexity."""
        # Truncate to 3000 chars for classification
        truncated = content[:3000]
        prompt = CONTENT_TYPE_DETECTION_PROMPT.format(content=truncated)
        response = await _call_llm(
            self._model,
            self._api_key,
            prompt,
            "detect_content_type",
            tracker,
            temperature=0.1,
            max_tokens=500,
        )
        return _parse_json_response(response, "detect_content_type")

    async def _step_extract_structure(
        self, content: str, content_type: str, language: str, tracker: TokenTracker
    ) -> str:
        """Step 2: Restructure content into clean markdown."""
        prompt = STRUCTURE_EXTRACTION_PROMPT.format(
            content=content, content_type=content_type, language=language
        )
        return await _call_llm(
            self._model,
            self._api_key,
            prompt,
            "extract_structure",
            tracker,
            max_tokens=self._max_tokens,
        )

    async def _step_enrich_content(
        self, structured_text: str, tracker: TokenTracker
    ) -> dict[str, Any]:
        """Step 3: Flag quality issues with inline annotations."""
        prompt = CONTENT_ENRICHMENT_PROMPT.format(content=structured_text)
        response = await _call_llm(
            self._model,
            self._api_key,
            prompt,
            "enrich_content",
            tracker,
            max_tokens=self._max_tokens,
        )
        return _parse_json_response(response, "enrich_content")

    async def _step_cross_document_intelligence(
        self,
        enriched_text: str,
    ) -> list[dict[str, Any]]:
        """Step 4: Cross-document intelligence (stub — implemented in CP-07)."""
        return []

    async def _step_prepare_chunks(
        self,
        enriched_text: str,
        content_type: str,
        language: str,
        tracker: TokenTracker,
    ) -> dict[str, Any]:
        """Step 5: Split into RAG-ready chunks with metadata."""
        prompt = CHUNK_PREPARATION_PROMPT.format(
            content=enriched_text, content_type=content_type, language=language
        )
        response = await _call_llm(
            self._model,
            self._api_key,
            prompt,
            "prepare_chunks",
            tracker,
            max_tokens=self._max_tokens,
        )
        return _parse_json_response(response, "prepare_chunks")

    async def _step_generate_quality_report(
        self,
        content_type: str,
        chunks: list[dict[str, Any]],
        enrichment_flags: list[dict[str, Any]],
        tracker: TokenTracker,
    ) -> dict[str, Any]:
        """Step 6: Generate quality assessment report."""
        prompt = QUALITY_REPORT_PROMPT.format(
            content_type=content_type,
            num_chunks=len(chunks),
            enrichment_flags=json.dumps(enrichment_flags, default=str),
        )
        response = await _call_llm(
            self._model,
            self._api_key,
            prompt,
            "quality_report",
            tracker,
            temperature=0.1,
            max_tokens=1000,
        )
        return _parse_json_response(response, "quality_report")


# ---------------------------------------------------------------------------
# Pipeline entry point (called by Celery task)
# ---------------------------------------------------------------------------


async def run_transform_pipeline(
    staged_content_id: str,
    corpus_id: str,
    settings: Settings,
    model_override: str | None = None,
) -> None:
    """Full AI transformation pipeline entry point.

    Loads staged content from DB, runs the 6-step transformer,
    writes StagedDocument + StagedChunk records, updates ai_status.

    Args:
        staged_content_id: UUID string of the staged content.
        corpus_id: UUID string of the corpus.
        settings: Application settings.
        model_override: Optional LLM model override.
    """
    from callobuzz_rag.content_portal.models.staged_chunk import StagedChunk
    from callobuzz_rag.content_portal.models.staged_content import (
        AIProcessingStatus,
        SourceType,
        StagedContent,
    )
    from callobuzz_rag.content_portal.models.staged_document import (
        StagedDocument,
    )
    from callobuzz_rag.db.engine import create_engine, create_session_factory
    from callobuzz_rag.db.models.corpus import Corpus

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    try:
        async with session_factory() as session:
            # Load staged content
            content_id = uuid.UUID(staged_content_id)
            sc = await session.get(StagedContent, content_id)
            if sc is None:
                raise TransformError(f"StagedContent {staged_content_id} not found")

            # Mark as processing
            sc.ai_status = AIProcessingStatus.PROCESSING
            await session.commit()

            # Load corpus for config resolution
            cid = uuid.UUID(corpus_id)
            corpus = await session.get(Corpus, cid)
            if corpus is None:
                logger.error("Corpus %s not found", corpus_id)
                sc.ai_status = AIProcessingStatus.FAILED
                sc.ai_quality_report = {"error": f"Corpus {corpus_id} not found"}
                await session.commit()
                return

            # Resolve LLM config
            from callobuzz_rag.billing.byok import byok_allowed  # P5 plans

            model, api_key = _resolve_llm_config(
                corpus,
                settings,
                model_override,
                byok_allowed=await byok_allowed(session, corpus.tenant_id),  # P5 plans
            )

            # Download content from file storage
            from callobuzz_rag.storage.factory import object_storage

            storage = object_storage(settings)
            raw_bytes = await storage.download(settings.s3_bucket, sc.raw_storage_key)

            # Decode content
            if sc.source_type == SourceType.URL:
                # For URL type, the stored content is the URL itself
                url = raw_bytes.decode("utf-8").strip()
                content_text = await _fetch_url_content(url)
            else:
                content_text = raw_bytes.decode("utf-8")

            # Run transformer
            transformer = AITransformer(
                model=model,
                api_key=api_key,
                settings=settings,
                max_tokens_per_step=settings.portal_ai_max_tokens_per_transform // 6,
            )
            result = await transformer.transform(content_text)

            # Write StagedDocument
            doc = StagedDocument(
                workspace_id=sc.workspace_id,
                staged_content_id=content_id,
                title=sc.original_filename or "Untitled",
                structured_text=result.structured_text,
                metadata_=result.metadata,
            )
            session.add(doc)
            await session.flush()

            # Write StagedChunks
            for chunk_data in result.chunks:
                chunk = StagedChunk(
                    workspace_id=sc.workspace_id,
                    staged_document_id=doc.id,
                    text=chunk_data.get("text", ""),
                    chunk_index=chunk_data.get("chunk_index", 0),
                    metadata_=chunk_data.get("metadata"),
                    confidence_score=chunk_data.get("confidence_score"),
                    warnings={"items": chunk_data.get("warnings", [])},
                )
                session.add(chunk)

            # Update staged content status
            sc.ai_status = AIProcessingStatus.COMPLETED
            sc.ai_output_type = result.output_type
            sc.ai_quality_report = result.quality_report
            sc.token_usage = result.token_usage

            await session.commit()
            logger.info(
                "Transform completed for content %s: type=%s, chunks=%d, tokens=%d",
                staged_content_id,
                result.output_type,
                len(result.chunks),
                result.token_usage.get("total_tokens", 0),
            )

    except TransformError as e:
        # Re-raise transform errors after marking status
        async with session_factory() as session:
            sc = await session.get(StagedContent, uuid.UUID(staged_content_id))
            if sc:
                sc.ai_status = AIProcessingStatus.FAILED
                sc.ai_quality_report = {"error": str(e)}
                await session.commit()
        raise
    except Exception as e:
        logger.exception("Transform pipeline failed for content %s", staged_content_id)
        async with session_factory() as session:
            sc = await session.get(StagedContent, uuid.UUID(staged_content_id))
            if sc:
                sc.ai_status = AIProcessingStatus.FAILED
                sc.ai_quality_report = {"error": str(e)}
                await session.commit()
        raise TransformError(f"Transform pipeline failed: {e}") from e
    finally:
        await engine.dispose()


def _resolve_llm_config(
    corpus: Any,
    settings: Settings,
    model_override: str | None,
    *,
    byok_allowed: bool,  # P5 plans: required, a key the plan does not include is not used
) -> tuple[str, str]:
    """Resolve LLM model + API key for the transform pipeline.

    Priority: model_override → portal AI model → corpus llm_config → global.

    Args:
        corpus: Corpus DB model instance.
        settings: Application settings.
        model_override: Explicit model override (highest priority).

    Returns:
        Tuple of (model_string, api_key).

    Raises:
        TransformError: If no API key can be resolved.
    """
    from callobuzz_rag.content_portal.config import get_portal_ai_model

    corpus_config = corpus.llm_config or {}
    if not byok_allowed:  # P5 plans: the stored key is kept, never used
        corpus_config = usable_llm_config(corpus_config, allowed=False) or {}
        corpus_config.pop("llm_config", None)  # the nested form some stored configs use

    # Resolve model
    model = model_override or get_portal_ai_model(corpus_config, settings)

    # Resolve API key: corpus BYOK (encrypted) → global
    api_key = ""
    encrypted_key = corpus_config.get("api_key_encrypted")
    if encrypted_key and settings.encryption_key:
        try:
            from callobuzz_rag.core.secrets import FernetEncryptor

            encryptor = FernetEncryptor(settings.encryption_key)
            api_key = encryptor.decrypt(encrypted_key)
        except Exception:
            logger.warning("Failed to decrypt corpus BYOK key, falling back to global")

    if not api_key:
        api_key = settings.llm_api_key.get_secret_value()

    if not api_key:
        raise TransformError("No LLM API key available (no BYOK key and no global key)")

    return model, api_key


_BLOCKED_NETWORKS = [
    "127.",
    "10.",
    "172.16.",
    "172.17.",
    "172.18.",
    "172.19.",
    "172.20.",
    "172.21.",
    "172.22.",
    "172.23.",
    "172.24.",
    "172.25.",
    "172.26.",
    "172.27.",
    "172.28.",
    "172.29.",
    "172.30.",
    "172.31.",
    "192.168.",
    "169.254.",
    "0.",
    "fd",
    "fe80:",
    "::1",
    "localhost",
]

_MAX_URL_CONTENT_BYTES = 10 * 1024 * 1024  # 10 MB


async def _fetch_url_content(url: str) -> str:
    """Fetch content from a URL with SSRF protection and size limits.

    Args:
        url: The URL to fetch.

    Returns:
        The fetched text content.

    Raises:
        TransformError: If the fetch fails, URL is blocked, or content too large.
    """
    from urllib.parse import urlparse

    import httpx  # Deferred import

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise TransformError(f"URL scheme '{parsed.scheme}' not allowed (http/https only)")

    hostname = (parsed.hostname or "").lower()
    for blocked in _BLOCKED_NETWORKS:
        if hostname.startswith(blocked):
            raise TransformError(f"URL hostname '{hostname}' is blocked (private/internal)")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            content_length = len(response.content)
            if content_length > _MAX_URL_CONTENT_BYTES:
                raise TransformError(
                    f"URL content too large ({content_length} bytes, max {_MAX_URL_CONTENT_BYTES})"
                )
            return response.text
    except TransformError:
        raise
    except Exception as e:
        raise TransformError(f"Failed to fetch URL '{url}': {e}") from e
