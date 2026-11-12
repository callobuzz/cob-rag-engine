"""widget_keys: the website widget keys, the sites each may be used on, and their limits."""

from typing import Any, ClassVar

from callobuzz_rag.cob.tools.base import ReadTool, RouteCall, ToolContext, ToolResult
from callobuzz_rag.cob.tools.cards import esc, link_to, num, table, ts
from callobuzz_rag.cob.tools.http import ToolFailure, fetch
from callobuzz_rag.cob.tools.registry import register
from callobuzz_rag.cob.tools.resolve import Ambiguous, NotFound, clarify, list_kbs, pick_kbs
from callobuzz_rag.cob.types import CobRole
from callobuzz_rag.core.schemas import StrictRequest

MAX_KBS = 10
MAX_SITES = 2


class WidgetKeysArgs(StrictRequest):
    kb: str | None = None


def sites_text(origins: Any, language: str) -> str:
    sites = [str(o) for o in (origins or [])]
    if not sites:
        return ts("no_sites", language)
    shown = ", ".join(sites[:MAX_SITES])
    return shown + (f" +{len(sites) - MAX_SITES}" if len(sites) > MAX_SITES else "")


class WidgetKeysTool(ReadTool):
    name: ClassVar[str] = "widget_keys"
    description: ClassVar[str] = (
        "The website widget keys: name, the websites allowed to use each, rate limits, and "
        "whether it is active."
    )
    args_model: ClassVar[type[StrictRequest]] = WidgetKeysArgs
    roles: ClassVar[frozenset[CobRole]] = frozenset(
        {CobRole.OWNER, CobRole.MEMBER, CobRole.OPERATOR_OWNER, CobRole.ADMIN}
    )
    routes: ClassVar[tuple[RouteCall, ...]] = (
        RouteCall("GET", "/api/v1/corpora"),
        RouteCall("GET", "/api/v1/corpora/{corpus_id}/widget-keys"),
    )
    screen: ClassVar[str] = "/widget"

    async def answer(self, ctx: ToolContext, args: Any) -> ToolResult:
        lang = ctx.language
        kbs = await list_kbs(ctx)
        picked = await pick_kbs(ctx, args.kb, kbs)
        if isinstance(picked, NotFound):
            raise ToolFailure("not_found", f"**{esc(picked.name, 60)}**")
        if isinstance(picked, Ambiguous):
            return clarify(ctx, picked.candidates)
        rows: list[list[Any]] = []
        for kb in picked[:MAX_KBS]:
            body = await fetch(ctx.client, f"/api/v1/corpora/{kb.get('id')}/widget-keys")
            for key in body if isinstance(body, list) else []:
                limits = ts(
                    "widget_limits",
                    lang,
                    rate=num(key.get("rate_limit_per_minute")),
                    daily=num(key.get("daily_message_limit")),
                )
                rows.append(
                    [
                        key.get("name"),
                        kb.get("name"),
                        sites_text(key.get("allowed_origins"), lang),
                        limits,
                        ts("yes" if key.get("is_active") else "no", lang),
                    ]
                )
        if not rows:
            return ToolResult(
                markdown=ts("widget_none", lang), link=link_to(self, ctx, self.screen)
            )
        headers = [
            ts("h_name", lang),
            ts("h_knowledge_base", lang),
            ts("h_sites", lang),
            ts("h_limits", lang),
            ts("h_active", lang),
        ]
        sentence = ts("widget_sentence", lang, n=num(len(rows)))
        return ToolResult(
            markdown=f"{sentence}\n\n{table(headers, rows, language=lang)}",
            link=link_to(self, ctx, self.screen),
            data={"keys": len(rows)},
        )


register(WidgetKeysTool())
