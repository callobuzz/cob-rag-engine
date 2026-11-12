"""Which host served a call: OpenRouter's generation record (free metadata, not a model call).

OpenRouter names the serving provider in each streamed chunk (`provider`), but LiteLLM's
OpenRouter stream parser keeps only id/model/choices/usage, so the name is read back from
`GET /api/v1/generation?id=<response id>` (`provider_name`, `total_cost`,
`native_tokens_reasoning`). The record is published a while after the stream ends (seconds to
minutes), so a 404 is retried with growing delays for up to `total_s` seconds. The runner does
this in the background, after the answer is already saved, and `python -m benchmarks.answers
hosts` fills whatever is still missing later, for free.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

DEFAULT_DELAYS: tuple[float, ...] = (1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 13.0, 15.0)
DEFAULT_TOTAL_S = 60.0


@dataclass(frozen=True)
class GenerationMeta:
    host: str | None
    total_cost: float | None
    native_tokens_reasoning: int | None

    @property
    def empty(self) -> bool:
        return (
            self.host is None and self.total_cost is None and self.native_tokens_reasoning is None
        )


class HostLookup(Protocol):
    source: str

    async def lookup(self, generation_id: str) -> GenerationMeta: ...


class NoLookup:
    source = "unknown"

    async def lookup(self, generation_id: str) -> GenerationMeta:
        return GenerationMeta(None, None, None)


class OpenRouterLookup:
    source = "openrouter_generation"

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        *,
        delays: Sequence[float] = DEFAULT_DELAYS,
        total_s: float = DEFAULT_TOTAL_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        transport: Any = None,
    ) -> None:
        self._key = api_key
        self._base = (base_url or "https://openrouter.ai/api/v1").rstrip("/")
        self._delays = tuple(delays)
        self._total = total_s
        self._sleep = sleep
        self._clock = clock
        self._transport = transport

    async def lookup(self, generation_id: str) -> GenerationMeta:
        import httpx

        url = f"{self._base}/generation"
        headers = {"Authorization": f"Bearer {self._key}"}
        started = self._clock()
        attempt = 0
        async with httpx.AsyncClient(timeout=10.0, transport=self._transport) as client:
            while True:
                try:
                    resp = await client.get(url, params={"id": generation_id}, headers=headers)
                except httpx.HTTPError:
                    resp = None
                if resp is not None and resp.status_code == 200:
                    data = resp.json().get("data") or {}
                    cost = data.get("total_cost")
                    reasoning = data.get("native_tokens_reasoning")
                    return GenerationMeta(
                        data.get("provider_name"),
                        float(cost) if isinstance(cost, int | float) else None,
                        int(reasoning) if isinstance(reasoning, int) else None,
                    )
                if (
                    resp is not None
                    and resp.status_code not in (404, 429)
                    and resp.status_code < 500
                ):
                    break
                delay = self._delays[min(attempt, len(self._delays) - 1)] if self._delays else 0.0
                attempt += 1
                if self._clock() - started + delay > self._total:
                    break
                await self._sleep(delay)
        return GenerationMeta(None, None, None)
