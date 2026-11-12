"""Knowledge articles and FAQ entries a business writes itself (BIZ3)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from callobuzz_rag.core.schemas import StrictRequest

MAX_ARTICLE_TITLE_CHARS = 200
MAX_ARTICLE_BODY_CHARS = 50_000

ArticleKind = Literal["article", "faq"]


class ArticleRequest(StrictRequest):
    kind: ArticleKind = Field(
        description="`article`: a page of text (Markdown headings and bullets allowed). "
        "`faq`: the title is the question and the body the answer."
    )
    title: str = Field(..., min_length=1, max_length=MAX_ARTICLE_TITLE_CHARS)
    body: str = Field(..., min_length=1, max_length=MAX_ARTICLE_BODY_CHARS)

    @field_validator("title", "body")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class ArticleOut(BaseModel):
    id: UUID = Field(description="The article's own id")
    document_id: UUID | None = Field(
        description="The knowledge base document it became; null until it has been learned"
    )
    kind: ArticleKind
    title: str
    body: str | None = Field(None, description="The text as written (returned by GET)")
    status: Literal["queued", "ready"] = Field(
        description="`queued`: being learned; `ready`: your assistant can use it"
    )
    job_id: UUID | None = Field(None, description="The import job (create and edit only)")
    created_by: str
    created_at: datetime
    updated_at: datetime
