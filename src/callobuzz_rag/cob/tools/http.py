"""The in-process client COB's tools use: the engine's own routes, with the caller's credentials.

Every request goes through the real ASGI app, so the middleware, `resolve_scope`, owner rules,
plan caps and the audit trail run exactly as they do for the dashboard. The client carries the
caller's own `Authorization` header and never anyone else's. A tool may call only the routes it
declares (`ToolRouteNotAllowed` otherwise). Never used for the public COB.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

import httpx

from callobuzz_rag.core.exceptions import RAGKernelError

if TYPE_CHECKING:
    from callobuzz_rag.cob.tools.base import CobTool

logger = logging.getLogger(__name__)

BASE_URL: Final = "http://cob.internal"
TIMEOUT_S: Final = 10.0


class ToolRouteNotAllowed(RAGKernelError):  # noqa: N818
    """A tool asked for a route it does not declare."""


@dataclass(frozen=True)
class InternalResponse:
    status: int
    body: Any  # parsed JSON, or None when the answer was not JSON
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class ToolFailure(RAGKernelError):  # noqa: N818
    """A route did not give a tool what it needed. `kind` is how the card words it."""

    def __init__(self, kind: str, name: str | None = None) -> None:
        super().__init__(kind)
        self.kind = kind
        self.name = name


class InternalClient:
    def __init__(
        self,
        app: Any,
        authorization: str | None,
        *,
        client_ip: str | None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._app = app
        self._headers: dict[str, str] = dict(headers or {})
        if authorization:
            self._headers["Authorization"] = authorization
        self._client_ip = client_ip

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> InternalResponse:
        # the peer address is the person's own, so the inner request_client_ip sees it
        transport = httpx.ASGITransport(app=self._app, client=(self._client_ip or "127.0.0.1", 0))
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url=BASE_URL, timeout=TIMEOUT_S
            ) as client:
                response = await client.request(
                    method,
                    path,
                    params={k: v for k, v in (params or {}).items() if v is not None},
                    json=json,
                    headers=self._headers,
                )
        except httpx.HTTPError:
            logger.warning("COB's internal call %s %s failed", method, path, exc_info=True)
            return InternalResponse(status=599, body=None)
        try:
            body = response.json()
        except ValueError:
            body = None
        return InternalResponse(
            status=response.status_code, body=body, headers=dict(response.headers)
        )


class ToolClient:
    """An `InternalClient` that lets one tool call only its declared routes."""

    def __init__(self, tool: "CobTool", client: Any) -> None:
        from callobuzz_rag.api.permissions import _pattern

        self._client = client
        self._allowed = [(rc.method, _pattern(rc.path)) for rc in tool.routes]
        self._tool = tool.name

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> InternalResponse:
        bare = path.split("?", 1)[0]
        if not any(m == method.upper() and p.match(bare) for m, p in self._allowed):
            raise ToolRouteNotAllowed(f"COB tool {self._tool!r} may not call {method} {bare}")
        result: InternalResponse = await self._client.request(
            method, path, params=params, json=json
        )
        return result


def tool_client(tool: "CobTool", client: Any) -> ToolClient:
    return ToolClient(tool, client)


_ID = re.compile(r"^[0-9a-fA-F-]{32,36}$")


def is_uuid_text(value: str) -> bool:
    return bool(_ID.match(value.strip()))


async def fetch(
    client: Any,
    path: str,
    params: dict[str, Any] | None = None,
    *,
    accept: tuple[int, ...] = (),
) -> Any:
    """GET `path` and return the JSON body, or raise `ToolFailure` the way a card should word it:
    a refused owner-only route is `owner`, any other 403 `forbidden`, a 404 `not_found`, and a
    server error or timeout `unavailable`. `accept` lists extra statuses whose body is still
    data (the readiness probe answers 503 with its detail)."""
    response: InternalResponse = await client.request("GET", path, params=params)
    if response.ok or response.status in accept:
        return response.body
    code = response.body.get("code") if isinstance(response.body, dict) else None
    if response.status == 403:
        raise ToolFailure("owner" if code == "owner_required" else "forbidden")
    if response.status == 404:
        raise ToolFailure("not_found")
    raise ToolFailure("unavailable")
