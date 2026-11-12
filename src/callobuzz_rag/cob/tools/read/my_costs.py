"""my_costs: this month's questions, tokens and cost by where they came from, incl. COB."""

from datetime import date
from typing import Any, ClassVar

from callobuzz_rag.cob.tools.base import ReadTool, RouteCall, ToolContext, ToolResult
from callobuzz_rag.cob.tools.cards import link_to, money, num, table, ts
from callobuzz_rag.cob.tools.http import ToolFailure, fetch
from callobuzz_rag.cob.tools.registry import register
from callobuzz_rag.cob.types import CobRole
from callobuzz_rag.core.schemas import StrictRequest

SOURCE_NAMES = {
    "cob": "COB",
    "api": "API",
    "stream": "API (streaming)",
    "conversation": "Conversations",
    "assistant": "Assistant setup",
    "setup": "Setup",
    "eval": "Evaluation",
}


class MyCostsArgs(StrictRequest):
    pass


def source_label(row: dict[str, Any], language: str) -> str:
    key = str(row.get("key") or "")
    if key.startswith("widget:"):
        return ts("source_widget", language)
    return SOURCE_NAMES.get(key) or str(row.get("label") or key or "-")


class MyCostsTool(ReadTool):
    name: ClassVar[str] = "my_costs"
    description: ClassVar[str] = (
        "This month's usage by source (API, widget, conversations, COB): requests, tokens, "
        "cost, and what was spent on the person's own model key."
    )
    args_model: ClassVar[type[StrictRequest]] = MyCostsArgs
    roles: ClassVar[frozenset[CobRole]] = frozenset(
        {CobRole.OWNER, CobRole.MEMBER, CobRole.OPERATOR_OWNER}
    )
    routes: ClassVar[tuple[RouteCall, ...]] = (RouteCall("GET", "/api/v1/usage"),)
    screen: ClassVar[str] = "/usage"

    async def answer(self, ctx: ToolContext, args: Any) -> ToolResult:
        lang = ctx.language
        start = date(ctx.now.year, ctx.now.month, 1).isoformat()
        body = await fetch(ctx.client, "/api/v1/usage", {"start": start, "group_by": "source"})
        rows_in = [r for r in ((body or {}).get("rows") or []) if isinstance(r, dict)]
        rate = (body or {}).get("usd_inr_rate")
        link = link_to(self, ctx, self.screen)
        if not rows_in:
            return ToolResult(markdown=ts("costs_none", lang), link=link, data={"requests": 0})
        totals = (body or {}).get("totals") or {}
        rows = [
            [
                source_label(r, lang),
                num(r.get("requests")),
                num(int(r.get("prompt_tokens") or 0) + int(r.get("completion_tokens") or 0)),
                money(r.get("cost_usd") if r.get("cost_known", True) else None, rate, lang),
            ]
            for r in rows_in
        ]
        headers = [
            ts("h_source", lang),
            ts("h_questions", lang),
            ts("h_tokens", lang),
            ts("h_cost", lang),
        ]
        sentence = ts(
            "costs_sentence",
            lang,
            requests=num(totals.get("requests")),
            cost=money(
                totals.get("cost_usd") if totals.get("cost_known", True) else None, rate, lang
            ),
        )
        text = f"{sentence}\n\n{table(headers, rows, language=lang)}"
        try:
            own = await fetch(
                ctx.client, "/api/v1/usage", {"start": start, "group_by": "source", "payer": "byok"}
            )
        except ToolFailure:
            own = None
        own_total = (own or {}).get("totals") or {}
        if own_total.get("requests"):
            text += "\n\n" + ts(
                "costs_own_key", lang, cost=money(own_total.get("cost_usd"), rate, lang)
            )
        return ToolResult(
            markdown=text,
            link=link,
            data={"requests": totals.get("requests"), "cost_usd": totals.get("cost_usd")},
        )


register(MyCostsTool())
