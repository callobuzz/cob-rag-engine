"""Serper.dev web search provider -- Google Search results via API."""

import logging

import httpx

from callobuzz_rag.websearch.base import WebSearchProvider, WebSearchResult

logger = logging.getLogger(__name__)

SERPER_API_URL = "https://google.serper.dev/search"


class SerperProvider(WebSearchProvider):
    def __init__(self, api_key: str, timeout: float = 10.0) -> None:
        self._api_key = api_key
        self._timeout = timeout

    async def search(self, query: str, max_results: int = 5) -> list[WebSearchResult]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                SERPER_API_URL,
                headers={
                    "X-API-KEY": self._api_key,
                    "Content-Type": "application/json",
                },
                json={"q": query, "num": max_results},
            )
            resp.raise_for_status()
            data = resp.json()

        results = []
        for item in data.get("organic", [])[:max_results]:
            results.append(
                WebSearchResult(
                    title=item.get("title", ""),
                    url=item.get("link", ""),
                    snippet=item.get("snippet", ""),
                    score=item.get("position", 0),
                )
            )
        return results

    async def health_check(self) -> bool:
        try:
            await self.search("test", max_results=1)
            return True
        except Exception:
            return False
