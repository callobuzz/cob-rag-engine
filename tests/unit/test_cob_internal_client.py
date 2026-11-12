"""COB's in-process client and the tool registry: the caller's own credentials, declared routes only."""

from typing import Any, ClassVar

import pytest

from callobuzz_rag.cob.tools.base import ReadTool, RouteCall, ToolContext, ToolResult
from callobuzz_rag.cob.tools.http import InternalClient, ToolRouteNotAllowed, tool_client
from callobuzz_rag.cob.tools.registry import REGISTRY, register, tools_for
from callobuzz_rag.cob.types import CobRole
from callobuzz_rag.core.schemas import StrictRequest
from tests.unit.cob_tool_helpers import FakeInternalClient
from tests.unit.scope_harness import scoped_app


class _Args(StrictRequest):
    pass


class _Stub(ReadTool):
    name: ClassVar[str] = "stub_tool"
    description: ClassVar[str] = "A stub."
    args_model: ClassVar[type[StrictRequest]] = _Args
    roles: ClassVar[frozenset[CobRole]] = frozenset({CobRole.OWNER})
    routes: ClassVar[tuple[RouteCall, ...]] = (
        RouteCall("GET", "/api/v1/corpora/{corpus_id}/widget-keys"),
    )
    screen: ClassVar[str] = "/widget"

    async def answer(self, ctx: ToolContext, args: Any) -> ToolResult:
        return ToolResult(markdown="ok")


async def test_internal_call_runs_the_real_middleware() -> None:
    with scoped_app() as (app, _world):
        client = InternalClient(
            app, None, client_ip="203.0.113.9", headers={"X-API-Key": "owner_a"}
        )
        r = await client.request("GET", "/api/v1/corpora")
    assert r.status == 200
    assert isinstance(r.body, dict) and "items" in r.body


async def test_internal_call_with_member_token_hits_the_owner_rule() -> None:
    with scoped_app() as (app, _world):
        client = InternalClient(app, None, client_ip="x", headers={"X-API-Key": "member_a"})
        r = await client.request("GET", "/api/v1/tenants/me/keys")
    assert r.status == 403


async def test_internal_call_sends_only_the_callers_own_header() -> None:
    seen: dict[str, str] = {}
    seen_client: list[Any] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        seen_client.extend(scope["client"])
        seen.update({k.decode(): v.decode() for k, v in scope["headers"]})
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    r = await InternalClient(app, "Bearer abc", client_ip="198.51.100.4").request("GET", "/x")
    assert r.status == 200 and r.body == {}
    assert seen["authorization"] == "Bearer abc"
    assert "x-forwarded-for" not in seen
    assert seen_client[0] == "198.51.100.4"


async def test_tool_may_call_only_its_declared_routes() -> None:
    tool = _Stub()
    scoped = tool_client(tool, FakeInternalClient())
    with pytest.raises(ToolRouteNotAllowed):
        await scoped.request("GET", "/api/v1/admin/tenants")
    with pytest.raises(ToolRouteNotAllowed):
        await scoped.request("DELETE", "/api/v1/corpora/9f0b/widget-keys")
    fake = FakeInternalClient()
    fake.reply("GET", "/api/v1/corpora/{corpus_id}/widget-keys", [])
    r = await tool_client(tool, fake).request("GET", "/api/v1/corpora/9f0b/widget-keys")
    assert r.status == 200


def test_registering_a_name_twice_is_refused() -> None:
    tool = _Stub()
    register(tool)
    try:
        with pytest.raises(ValueError, match="twice"):
            register(_Stub())
        assert tools_for(CobRole.OWNER)[-1] is tool
        assert tool not in tools_for(CobRole.MEMBER)
    finally:
        REGISTRY.pop("stub_tool", None)


def test_tools_for_public_is_empty() -> None:
    register(_Stub())
    try:
        assert tools_for(CobRole.PUBLIC) == []
    finally:
        REGISTRY.pop("stub_tool", None)
