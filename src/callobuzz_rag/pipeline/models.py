"""Pipeline data models."""

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class JobStatus(StrEnum):
    QUEUED = "queued"
    UPLOADING = "uploading"
    PARSING = "parsing"
    CHUNKING = "chunking"
    ENRICHING = "enriching"
    EMBEDDING = "embedding"
    STORING = "storing"
    COMPLETED = "completed"
    FAILED = "failed"
    # Phase 6: Re-ingestion states
    PENDING_REVIEW = "pending_review"
    EXPIRED = "expired"
    # The owner dropped a near-duplicate upload: nothing was indexed, the file is deleted.
    SKIPPED = "skipped"


class IngestMode(StrEnum):
    """Ingestion mode controlling duplicate/version behavior."""

    AUTO = "auto"
    REPLACE = "replace"
    NEW = "new"
    VERSION = "version"


@dataclass
class PipelineResult:
    """Result of a pipeline execution."""

    job_id: uuid.UUID
    status: JobStatus
    document_id: uuid.UUID | None = None
    chunks_created: int = 0
    vectors_stored: int = 0
    error: str | None = None
    failed_at_step: str | None = None
    warnings: list[str] = field(default_factory=list)
    # Timing metrics (populated by orchestrator)
    parse_duration_ms: int = 0
    chunk_duration_ms: int = 0
    enrich_duration_ms: int = 0
    embed_duration_ms: int = 0
    store_duration_ms: int = 0
    total_duration_ms: int = 0
    # Phase 6: Version tracking
    version_number: int | None = None
    replaced_document_id: uuid.UUID | None = None
    similar_documents: list[dict[str, Any]] | None = None
