"""scraper/deliver/sender.py — Deliver scraped content to the RAG engine.

Pages go to exactly one place: ``{rag_url}/api/v1/ingest`` of the configured engine.
There is deliberately no parameter that could point a delivery anywhere else.
"""

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

# The engine's reason is kept with the page; a longer body is cut to this length.
MAX_DETAIL_CHARS = 300


@dataclass
class DeliveryResult:
    """Result of delivering a single page to the target API."""

    page_id: str
    success: bool
    error: str | None = None
    # The engine's HTTP status and error code when it refused the page.
    status_code: int | None = None
    code: str | None = None
    detail: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class DeliverySender:
    """Send verified pages to the engine's ingest API as multipart file uploads."""

    def __init__(self, rag_url: str, data_dir: str, rate_limit: int = 2) -> None:
        self.ingest_url = rag_url.rstrip("/") + "/api/v1/ingest"
        self.data_dir = data_dir
        self._semaphore = asyncio.Semaphore(rate_limit)

    async def deliver_page(
        self,
        job_id: str,
        page_id: str,
        title: str,
        auth_headers: dict[str, str],
        corpus_id: str,
    ) -> DeliveryResult:
        """Upload a page's markdown content to the engine's ingest endpoint.

        ``auth_headers`` is the caller's own credential header (``X-API-Key`` or
        ``Authorization: Bearer``), so the engine enforces the caller's access.
        """
        async with self._semaphore:
            file_path = Path(self.data_dir) / "jobs" / job_id / f"{page_id}.md"

            if not file_path.exists():
                return DeliveryResult(
                    page_id=page_id,
                    success=False,
                    error=f"Content file not found: {file_path}",
                )

            content = file_path.read_text(encoding="utf-8")
            filename = self._sanitize_filename(title) + ".md"

            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
                    response = await client.post(
                        self.ingest_url,
                        headers=auth_headers,
                        files={"file": (filename, content.encode("utf-8"), "text/markdown")},
                        data={"corpus_id": corpus_id},
                    )

                    if response.status_code >= 400:
                        return self._refused(page_id, response)

                    return DeliveryResult(page_id=page_id, success=True)

            except httpx.HTTPError as exc:
                return DeliveryResult(
                    page_id=page_id,
                    success=False,
                    error=f"RAG engine unreachable: {type(exc).__name__}",
                )

    @staticmethod
    def _refused(page_id: str, response: httpx.Response) -> DeliveryResult:
        """A failed result that keeps the engine's reason (e.g. the plan's document limit)."""
        detail: str | None = None
        code: str | None = None
        extra: dict[str, Any] = {}
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            if isinstance(body.get("detail"), str):
                detail = body["detail"][:MAX_DETAIL_CHARS]
            if isinstance(body.get("code"), str) and body["code"]:
                code = body["code"]
            extra = {
                k: v
                for k, v in body.items()
                if k not in ("detail", "code") and isinstance(v, str | int | float | bool | None)
            }
        error = f"RAG engine returned HTTP {response.status_code}"
        if detail:
            error = f"{error}: {detail}"
        return DeliveryResult(
            page_id=page_id,
            success=False,
            error=error,
            status_code=response.status_code,
            code=code,
            detail=detail,
            extra=extra,
        )

    @staticmethod
    def _sanitize_filename(title: str) -> str:
        """Convert a page title to a safe filename."""
        if not title:
            return "untitled"
        # Replace non-alphanumeric chars (except hyphens/underscores) with hyphens
        safe = re.sub(r"[^\w\-]", "-", title.lower())
        # Collapse multiple hyphens
        safe = re.sub(r"-+", "-", safe).strip("-")
        # Truncate to reasonable length
        return safe[:80] or "untitled"
