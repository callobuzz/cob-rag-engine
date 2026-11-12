"""What a COB tool is: a named, role-gated capability that calls the engine's own routes.

A tool never reaches into the database or a service layer. It calls the real routes through
the caller's own `InternalClient`, so sign-in checks, owner rules, workspace scoping and plan
caps apply exactly as they do for the dashboard (README D1), and a route that changes is picked
up here without a second edit.

A `ReadTool` turns the route answers into a small Markdown card and a link to the screen. A card
never carries a secret, a hash, a contact field or another person's e-mail.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from callobuzz_rag.api.schemas.cob import CobContext, CobLinkBlock
from callobuzz_rag.cob.types import CobCaller, CobRole
from callobuzz_rag.conversation.schema import QuickReply
from callobuzz_rag.core.schemas import StrictRequest

if TYPE_CHECKING:
    from callobuzz_rag.cob.tools.http import ToolClient

HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]


@dataclass(frozen=True)
class RouteCall:
    method: HttpMethod
    path: str  # a ROUTE_OPS key, e.g. "/api/v1/corpora/{corpus_id}/widget-keys"


@dataclass
class ToolContext:
    caller: CobCaller
    client: "ToolClient"
    language: str  # the language of COB's reply
    page: CobContext | None
    now: datetime
    # The person's own words: a knowledge base they name there is understood without a model.
    question: str = ""


@dataclass
class ToolResult:
    markdown: str  # the rendered card; no secrets, no contact data
    link: CobLinkBlock | None = None
    quick_replies: list[QuickReply] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)  # for tests and the owner trace only
    ok: bool = True


class CobTool(ABC):
    name: ClassVar[str]
    kind: ClassVar[Literal["read", "action"]]
    description: ClassVar[str]  # one English line for the router
    args_model: ClassVar[type[StrictRequest]]
    roles: ClassVar[frozenset[CobRole]]
    routes: ClassVar[tuple[RouteCall, ...]]
    destructive: ClassVar[bool] = False
    owner_only: ClassVar[bool] = False


class ReadTool(CobTool):
    kind: ClassVar[Literal["read", "action"]] = "read"
    # The dashboard screen the card links to when the tool cannot say more.
    screen: ClassVar[str]

    @abstractmethod
    async def answer(self, ctx: ToolContext, args: Any) -> ToolResult:
        """The card for `args`. Route failures raise `ToolFailure`; `run` words them."""

    async def run(self, ctx: ToolContext, args: StrictRequest) -> ToolResult:
        from callobuzz_rag.cob.tools.cards import link_to, ts
        from callobuzz_rag.cob.tools.http import ToolFailure

        try:
            return await self.answer(ctx, args)
        except ToolFailure as failure:
            text = {
                "owner": ts("owner_only", ctx.language),
                "forbidden": ts("forbidden", ctx.language),
                "not_found": ts("not_found", ctx.language, name=failure.name or ""),
            }.get(failure.kind, ts("unavailable", ctx.language))
            link = (
                None if failure.kind in ("owner", "forbidden") else link_to(self, ctx, self.screen)
            )
            return ToolResult(markdown=text, link=link, ok=False, data={"failure": failure.kind})


class ActionTool(CobTool):
    """A tool that changes something, after the person confirms a card (COB5)."""

    kind: ClassVar[Literal["read", "action"]] = "action"

    @abstractmethod
    async def propose(self, ctx: ToolContext, args: Any) -> Any: ...

    @abstractmethod
    async def execute(self, ctx: ToolContext, args: Any) -> Any: ...
