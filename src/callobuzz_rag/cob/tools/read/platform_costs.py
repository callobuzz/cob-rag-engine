"""platform_costs (operator): what the engine cost today and this month, and who and what drove it."""

from typing import Any, ClassVar

from callobuzz_rag.cob.tools.base import ReadTool, RouteCall, ToolContext, ToolResult
from callobuzz_rag.cob.tools.cards import link_to, money, table, ts
from callobuzz_rag.cob.tools.http import fetch
from callobuzz_rag.cob.tools.registry import register
from callobuzz_rag.cob.types import CobRole
from callobuzz_rag.core.schemas import StrictRequest

TOP = 5


class PlatformCostsArgs(StrictRequest):
    pass


def _cost(row: Any, rate: Any, language: str) -> str:
    if not isinstance(row, dict):
        return money(None, rate, language)
    return money(row.get("cost_usd") if row.get("cost_known", True) else None, rate, language)


def _top(rows: Any, rate: Any, language: str) -> list[list[Any]]:
    ranked = sorted(
        (r for r in (rows or []) if isinstance(r, dict)),
        key=lambda r: float(r.get("cost_usd") or 0),
        reverse=True,
    )
    return [[r.get("label") or r.get("key") or "-", _cost(r, rate, language)] for r in ranked[:TOP]]


class PlatformCostsTool(ReadTool):
    name: ClassVar[str] = "platform_costs"
    description: ClassVar[str] = (
        "Operator only: the platform's model cost today and this month, with the top "
        "customers and models by cost."
    )
    args_model: ClassVar[type[StrictRequest]] = PlatformCostsArgs
    roles: ClassVar[frozenset[CobRole]] = frozenset({CobRole.ADMIN})
    routes: ClassVar[tuple[RouteCall, ...]] = (RouteCall("GET", "/api/v1/admin/costs"),)
    screen: ClassVar[str] = "/admin/costs"

    async def answer(self, ctx: ToolContext, args: Any) -> ToolResult:
        lang = ctx.language
        body = await fetch(ctx.client, "/api/v1/admin/costs") or {}
        rate = body.get("usd_inr_rate")
        sentence = ts(
            "platform_sentence",
            lang,
            today=_cost(body.get("today"), rate, lang),
            month=_cost(body.get("month"), rate, lang),
        )
        text = sentence
        customers = _top(body.get("by_tenant"), rate, lang)
        if customers:
            text += "\n\n" + table(
                [ts("h_customer", lang), ts("h_cost", lang)], customers, language=lang
            )
        models = _top(body.get("by_model"), rate, lang)
        if models:
            text += "\n\n" + table([ts("h_model", lang), ts("h_cost", lang)], models, language=lang)
        raw_month = body.get("month")
        month: dict[str, Any] = raw_month if isinstance(raw_month, dict) else {}
        return ToolResult(
            markdown=text,
            link=link_to(self, ctx, self.screen),
            data={"month_usd": month.get("cost_usd")},
        )


register(PlatformCostsTool())
