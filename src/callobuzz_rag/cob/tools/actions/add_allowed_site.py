"""add_allowed_site: let one more website use an existing widget key."""

import uuid
from typing import Any, ClassVar

from callobuzz_rag.cob.tools.actions.common import (
    ActionBase,
    ActionOutcome,
    ProposalDraft,
    ask,
    one_kb,
    origin_of,
    pick_by_name,
    site_in,
)
from callobuzz_rag.cob.tools.base import RouteCall, ToolContext, ToolResult
from callobuzz_rag.cob.tools.cards import esc, ts
from callobuzz_rag.cob.tools.http import ToolFailure, fetch
from callobuzz_rag.cob.tools.registry import register
from callobuzz_rag.cob.types import CobRole
from callobuzz_rag.conversation.schema import QuickReply
from callobuzz_rag.core.schemas import StrictRequest

PATH = "/api/v1/corpora/{corpus_id}/widget-keys/{key_id}"


class AddAllowedSiteArgs(StrictRequest):
    kb: str | None = None
    site: str | None = None
    key: str | None = None


class AddAllowedSiteExec(StrictRequest):
    corpus_id: uuid.UUID
    kb_name: str
    key_id: uuid.UUID
    key_name: str
    site: str


class AddAllowedSiteTool(ActionBase):
    name: ClassVar[str] = "add_allowed_site"
    description: ClassVar[str] = (
        "Allow one more website on an existing widget key (args.site, args.kb, args.key is the "
        "widget key's name)."
    )
    args_model: ClassVar[type[StrictRequest]] = AddAllowedSiteArgs
    exec_model: ClassVar[type[StrictRequest]] = AddAllowedSiteExec
    roles: ClassVar[frozenset[CobRole]] = frozenset({CobRole.OWNER, CobRole.OPERATOR_OWNER})
    routes: ClassVar[tuple[RouteCall, ...]] = (
        RouteCall("PATCH", PATH),
        RouteCall("GET", "/api/v1/corpora"),
        RouteCall("GET", "/api/v1/corpora/{corpus_id}/widget-keys"),
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
        try:
            body = await fetch(ctx.client, f"/api/v1/corpora/{kb.get('id')}/widget-keys")
        except ToolFailure:
            return ToolResult(markdown=ts("unavailable", lang), ok=False)
        keys = [k for k in (body if isinstance(body, list) else []) if isinstance(k, dict)]
        if not keys:
            return ToolResult(
                markdown=ts("no_widget_keys", lang, kb=esc(kb.get("name"), 60)), ok=False
            )
        chosen = pick_by_name(keys, args.key, ctx.question) if len(keys) > 1 else keys
        if len(chosen) != 1:
            names = ", ".join(f"**{esc(k.get('name'), 40)}**" for k in keys[:5])
            replies = [
                QuickReply(
                    label=str(k.get("name"))[:20] or "?",
                    message=f"{ctx.question} ({k.get('name')})"[:200],
                )
                for k in keys[:5]
            ]
            return ToolResult(
                markdown=ts("ask_widget_key", lang, names=names), quick_replies=replies
            )
        key = chosen[0]
        existing = [str(o) for o in (key.get("allowed_origins") or [])]
        if origin in existing or "*" in existing:
            return ToolResult(
                markdown=ts(
                    "site_already_allowed",
                    lang,
                    site=esc(origin, 100),
                    key=esc(key.get("name"), 60),
                ),
                link=self.link(ctx),
            )
        return ProposalDraft(
            title=ts("title_add_allowed_site", lang),
            summary=ts(
                "sum_add_allowed_site",
                lang,
                site=esc(origin, 100),
                key=esc(key.get("name"), 60),
                kb=esc(kb.get("name"), 60),
            ),
            args=[
                (ts("a_kb", lang), str(kb.get("name"))),
                (ts("a_key", lang), str(key.get("name"))),
                (ts("a_site", lang), origin),
            ],
            risk="low",
            exec_args={
                "corpus_id": str(kb.get("id")),
                "kb_name": str(kb.get("name")),
                "key_id": str(key.get("id")),
                "key_name": str(key.get("name")),
                "site": origin,
            },
        )

    async def execute(self, ctx: ToolContext, exec_args: dict[str, Any]) -> ActionOutcome:
        # The key's sites are read again, so a site added since the card was made is kept.
        path = f"/api/v1/corpora/{exec_args['corpus_id']}/widget-keys"
        lang = ctx.language

        def stop(status: int, code: str, message: str) -> ActionOutcome:
            # Nothing is written: a guess at the key's sites would replace the real ones.
            return ActionOutcome(
                method="PATCH", path=PATH, status=status, ok=False, message=message, code=code
            )

        missing = ts("not_found", lang, name=f"**{esc(exec_args['key_name'], 60)}**")
        try:
            body = await fetch(ctx.client, path)
        except ToolFailure as failure:
            if failure.kind in ("owner", "forbidden"):
                return stop(403, "forbidden", ts("forbidden", lang))
            if failure.kind == "not_found":
                return stop(404, "not_found", missing)
            return stop(503, "unavailable", ts("unavailable", lang))
        found = [
            k
            for k in (body if isinstance(body, list) else [])
            if isinstance(k, dict) and str(k.get("id")) == exec_args["key_id"]
        ]
        if not found:
            return stop(404, "not_found", missing)
        origins = [str(o) for o in found[0].get("allowed_origins") or []]
        if "*" in origins or exec_args["site"] in origins:
            return ActionOutcome(
                method="PATCH",
                path=PATH,
                status=200,
                ok=True,
                message=ts(
                    "site_already_allowed",
                    lang,
                    site=esc(exec_args["site"], 100),
                    key=esc(exec_args["key_name"], 60),
                ),
                link=self.link(ctx),
                result={"key_id": exec_args["key_id"]},
            )
        origins.append(exec_args["site"])
        response = await ctx.client.request(
            "PATCH", f"{path}/{exec_args['key_id']}", json={"allowed_origins": origins}
        )
        return self.done(
            ctx,
            response,
            "PATCH",
            PATH,
            ts(
                "done_add_allowed_site",
                ctx.language,
                site=esc(exec_args["site"], 100),
                key=esc(exec_args["key_name"], 60),
            ),
            result={"key_id": exec_args["key_id"]},
        )


register(AddAllowedSiteTool())
