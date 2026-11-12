"""Response models for the in-app Docs section (tree, one page, search)."""

from pydantic import BaseModel, Field


class DocsTreePage(BaseModel):
    slug: str
    title: str
    route: str | None = None
    summary: str = ""


class DocsTopic(BaseModel):
    id: str
    title: str
    pages: list[DocsTreePage]


class DocsTree(BaseModel):
    version: str = Field(..., description="Docs version: engine version plus a content hash")
    topics: list[DocsTopic]


class DocsTocItem(BaseModel):
    anchor: str
    title: str
    level: int


class DocsNeighbor(BaseModel):
    slug: str
    title: str


class DocPageOut(BaseModel):
    slug: str
    title: str
    topic: str
    route: str | None = None
    summary: str = ""
    markdown: str
    toc: list[DocsTocItem]
    version: str
    public_url: str | None = None
    prev: DocsNeighbor | None = None
    next: DocsNeighbor | None = None


class DocsSearchItem(BaseModel):
    slug: str
    anchor: str
    title: str
    snippet: str
    route: str | None = None
    score: float


class DocsSearchOut(BaseModel):
    items: list[DocsSearchItem]
