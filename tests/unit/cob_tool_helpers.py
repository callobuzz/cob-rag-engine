"""Test doubles for COB's tools: a client that answers from recorded route JSON, and contexts.

`FakeInternalClient.reply(method, path, body, status=200)` records an answer for a route (the
path may be a template such as `/api/v1/corpora/{corpus_id}/keys`); a tool calling a route that
has no recorded answer gets a 599, like a timeout. `calls` lists every request made.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from callobuzz_rag.api.permissions import _pattern
from callobuzz_rag.cob.tools.base import CobTool, ToolContext
from callobuzz_rag.cob.tools.http import InternalResponse, tool_client
from callobuzz_rag.cob.types import CobCaller, CobRole

NOW = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)


@dataclass
class _Reply:
    method: str
    pattern: Any
    body: Any
    status: int
    when: dict[str, Any] | None = None  # only a request carrying these params


class FakeInternalClient:
    def __init__(self) -> None:
        self._replies: list[_Reply] = []
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.bodies: list[Any] = []  # COB5: the JSON body of each call, in order

    def reply(
        self,
        method: str,
        path: str,
        body: Any,
        status: int = 200,
        when: dict[str, Any] | None = None,
    ) -> None:
        self._replies.insert(0, _Reply(method, _pattern(path), body, status, when))

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> InternalResponse:
        bare = path.split("?", 1)[0]
        self.calls.append((method, bare, dict(params or {})))
        self.bodies.append(json)
        for r in self._replies:
            if r.when and any((params or {}).get(k) != v for k, v in r.when.items()):
                continue
            if r.method == method and r.pattern.match(bare):
                return InternalResponse(status=r.status, body=r.body)
        return InternalResponse(status=599, body=None)


class FakeToolContext:
    """A `ToolContext` whose client is a fake; `for_tool` re-wraps it for one tool's routes."""

    def __init__(self, role: CobRole, language: str = "en", question: str = "") -> None:
        self.client = FakeInternalClient()
        self.caller = CobCaller(role, uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "Bearer test")
        self.language = language
        self.question = question

    def for_tool(self, tool: CobTool) -> ToolContext:
        return ToolContext(
            caller=self.caller,
            client=tool_client(tool, self.client),
            language=self.language,
            page=None,
            now=NOW,
            question=self.question,
        )


class SpyLLM:
    """Stands in for the router's provider: a scripted JSON answer, and a count of calls."""

    def __init__(self, answer: Any = None, *, delay: float = 0.0, raw: str | None = None) -> None:
        self.answer = answer
        self.delay = delay
        self.raw = raw
        self.calls = 0
        self.prompts: list[str] = []

    def fast_call_options(self) -> dict[str, Any]:
        return {"model": "fast-model"}

    async def complete_json(self, messages: Any, **kwargs: Any) -> Any:
        import asyncio
        import json

        from callobuzz_rag.llm.models import LLMResponse, LLMUsage

        self.calls += 1
        self.prompts.append("\n".join(m.content for m in messages))
        if self.delay:
            await asyncio.sleep(self.delay)
        content = self.raw if self.raw is not None else json.dumps(self.answer)
        return LLMResponse(
            content=content,
            model="fast-model",
            usage=LLMUsage(prompt_tokens=40, completion_tokens=9, total_tokens=49),
        )
