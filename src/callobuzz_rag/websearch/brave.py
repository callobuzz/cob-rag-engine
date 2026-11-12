"""Brave Search API provider."""

import logging

import httpx

from callobuzz_rag.websearch.base import WebSearchProvider, WebSearchResult

logger = logging.getLogger(__name__)

BRAVE_API_URL = "https://api.search.brave.com/res/v1/web/search"


class BraveProvider(WebSearchProvider):
    def __init__(self, api_key: str, timeout: float = 10.0) -> None:
        self._api_key = api_key
        self._timeout = timeout

    async def search(self, query: str, max_results: int = 5) -> list[WebSearchResult]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                BRAVE_API_URL,
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self._api_key,
                },
                params={"q": query, "count": max_results},
            )
            resp.raise_for_status()
            data = resp.json()

        results = []
        for item in data.get("web", {}).get("results", [])[:max_results]:
            results.append(
                WebSearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("description", ""),
                )
            )
        return results

    async def health_check(self) -> bool:
        try:
            await self.search("test", max_results=1)
            return True
        except Exception:
            return False
