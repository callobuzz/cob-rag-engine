"""create_widget_key: a website widget key for a knowledge base, allowed on one site."""

import uuid
from typing import Any, ClassVar
from urllib.parse import urlsplit

from callobuzz_rag.cob.tools.actions.common import (
    ActionBase,
    ActionOutcome,
    ProposalDraft,
    ask,
    one_kb,
    origin_of,
    site_in,
)
from callobuzz_rag.cob.tools.base import RouteCall, ToolContext, ToolResult
from callobuzz_rag.cob.tools.cards import esc, ts
from callobuzz_rag.cob.tools.registry import register
from callobuzz_rag.cob.types import CobRole
from callobuzz_rag.core.schemas import StrictRequest

PATH = "/api/v1/corpora/{corpus_id}/widget-keys"


class CreateWidgetKeyArgs(StrictRequest):
    kb: str | None = None
    site: str | None = None
    name: str | None = None


class CreateWidgetKeyExec(StrictRequest):
    corpus_id: uuid.UUID
    kb_name: str
    name: str
    allowed_origins: list[str]


class CreateWidgetKeyTool(ActionBase):
    name: ClassVar[str] = "create_widget_key"
    description: ClassVar[str] = (
        "Create a website widget key for a knowledge base (args.kb, args.site is the website "
        "it may run on, args.name an optional label)."
    )
    args_model: ClassVar[type[StrictRequest]] = CreateWidgetKeyArgs
    exec_model: ClassVar[type[StrictRequest]] = CreateWidgetKeyExec
    roles: ClassVar[frozenset[CobRole]] = frozenset({CobRole.OWNER, CobRole.OPERATOR_OWNER})
    routes: ClassVar[tuple[RouteCall, ...]] = (
        RouteCall("POST", PATH),
        RouteCall("GET", "/api/v1/corpora"),
    )
    owner_only: ClassVar[bool] = True
    risk: ClassVar[Any] = "low"
    screen: ClassVar[str] = "/widget"

    async def propose(self, ctx: ToolContext, args: Any) -> ProposalDraft | ToolResult:
        lang = ctx.language
        origin = origin_of(args.site) if args.site else site_in(ctx.question)
        if origin is None:
            return ask(ctx, "ask_site")
        kb = await one_kb(ctx, args.kb)
        if isinstance(kb, ToolResult):
            return kb
        label = (args.name or urlsplit(origin).hostname or "Website").strip()[:100]
        return ProposalDraft(
            title=ts("title_create_widget_key", lang),
            summary=ts(
                "sum_create_widget_key",
                lang,
                name=esc(label, 100),
                kb=esc(kb.get("name"), 60),
                site=esc(origin, 100),
            ),
            args=[
                (ts("a_kb", lang), str(kb.get("name"))),
                (ts("a_name", lang), label),
                (ts("a_site", lang), origin),
            ],
            risk="low",
            exec_args={
                "corpus_id": str(kb.get("id")),
                "kb_name": str(kb.get("name")),
                "name": label,
                "allowed_origins": [origin],
            },
        )

    async def execute(self, ctx: ToolContext, exec_args: dict[str, Any]) -> ActionOutcome:
        response = await ctx.client.request(
            "POST",
            f"/api/v1/corpora/{exec_args['corpus_id']}/widget-keys",
            json={"name": exec_args["name"], "allowed_origins": exec_args["allowed_origins"]},
        )
        return self.done(
            ctx,
            response,
            "POST",
            PATH,
            ts(
                "done_create_widget_key",
                ctx.language,
                name=esc(exec_args["name"], 100),
                kb=esc(exec_args["kb_name"], 60),
            ),
            result={"corpus_id": exec_args["corpus_id"]},
        )


register(CreateWidgetKeyTool())
