"""Pydantic request/response schemas for the scraper API."""

from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class ScrapeRequest(BaseModel):
    """Request body for POST /scrape.

    Unknown fields are ignored (pydantic's default), so an older client that still
    sends ``target_url`` / ``target_api_key`` is not rejected — those values are
    simply never used. Delivery always goes to the configured engine.
    """

    sitemap_url: str | None = None
    urls: list[str] | None = None
    # Optional for a corpus key (defaults to its own corpus); required otherwise.
    target_corpus_id: str | None = None
    review_mode: bool = True
    max_pages: int | None = Field(default=None, ge=1, le=1000)

    @field_validator("target_corpus_id")
    @classmethod
    def corpus_id_is_uuid(cls, value: str | None) -> str | None:
        """Engine corpus ids are UUIDs; anything else never reaches a URL path."""
        if value is None or value == "":
            return None
        try:
            return str(UUID(value))
        except ValueError as exc:
            raise ValueError("target_corpus_id must be a UUID") from exc

    @model_validator(mode="after")
    def require_sitemap_or_urls(self) -> "ScrapeRequest":
        """At least one of sitemap_url or urls must be provided."""
        if not self.sitemap_url and not self.urls:
            raise ValueError("At least one of 'sitemap_url' or 'urls' is required")
        return self


class ScrapeResponse(BaseModel):
    """Response body for POST /scrape."""

    job_id: str
    status: str


class ApproveRequest(BaseModel):
    """Request body for POST /jobs/{job_id}/approve."""

    approve_all_verified: bool = False
    page_ids: list[str] | None = None
