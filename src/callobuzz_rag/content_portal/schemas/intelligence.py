"""Request/response schemas for corpus intelligence endpoints."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DuplicateMatchResponse(BaseModel):
    """A single duplicate match."""

    model_config = ConfigDict(from_attributes=True)

    existing_chunk_id: str
    existing_text: str
    similarity_score: float
    is_exact_duplicate: bool
    document_id: str | None = None
    file_name: str | None = None


class DuplicateReportResponse(BaseModel):
    """Duplicate detection results for a single staged chunk."""

    model_config = ConfigDict(from_attributes=True)

    staged_chunk_id: UUID
    staged_text_preview: str
    matches: list[DuplicateMatchResponse]
    has_duplicates: bool = False
    has_near_duplicates: bool = False


class ContradictionResponse(BaseModel):
    """A detected contradiction."""

    model_config = ConfigDict(from_attributes=True)

    existing_chunk_id: str
    existing_text: str
    new_text: str
    explanation: str
    severity: str
    confidence: float


class ContradictionReportResponse(BaseModel):
    """Contradiction detection results for a single staged chunk."""

    model_config = ConfigDict(from_attributes=True)

    staged_chunk_id: UUID
    staged_text_preview: str
    contradictions: list[ContradictionResponse]
    has_contradictions: bool = False


class GapTopicResponse(BaseModel):
    """A topic identified as a gap in the existing corpus."""

    model_config = ConfigDict(from_attributes=True)

    topic: str
    description: str
    confidence: float
    supporting_text: str


class GapAnalysisReportResponse(BaseModel):
    """Gap analysis results for a workspace's staged content."""

    model_config = ConfigDict(from_attributes=True)

    workspace_id: UUID
    new_topics: list[GapTopicResponse] = []
    covered_topics: list[str] = []
    has_new_knowledge: bool = False


class IntelligenceReportResponse(BaseModel):
    """Full intelligence analysis report."""

    model_config = ConfigDict(from_attributes=True)

    workspace_id: UUID
    corpus_id: UUID
    duplicate_reports: list[DuplicateReportResponse]
    contradiction_reports: list[ContradictionReportResponse]
    gap_report: GapAnalysisReportResponse | None = None
    total_duplicates: int = 0
    total_near_duplicates: int = 0
    total_contradictions: int = 0
    total_new_topics: int = 0


VALID_ANALYSIS_TYPES = {"duplicates", "contradictions", "gaps"}


class IntelligenceRequest(BaseModel):
    """Request body for intelligence analysis."""

    chunk_ids: list[UUID] | None = None
    analysis_types: list[str] = Field(
        default_factory=lambda: ["duplicates", "contradictions", "gaps"],
    )
    model: str | None = None

    @field_validator("analysis_types")
    @classmethod
    def validate_analysis_types(cls, v: list[str]) -> list[str]:
        invalid = set(v) - VALID_ANALYSIS_TYPES
        if invalid:
            raise ValueError(
                f"Invalid analysis types: {sorted(invalid)}. "
                f"Must be from: {sorted(VALID_ANALYSIS_TYPES)}"
            )
        return v
