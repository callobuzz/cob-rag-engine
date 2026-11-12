"""customers (operator): workspaces by status, the ones waiting for approval, and their plans.

Names, statuses, plans and counts only. A customer's owner e-mail addresses are never read
into a card."""

from collections import Counter
from typing import Any, ClassVar

from callobuzz_rag.cob.tools.base import ReadTool, RouteCall, ToolContext, ToolResult
from callobuzz_rag.cob.tools.cards import esc, link_to, num, table, ts
from callobuzz_rag.cob.tools.http import fetch
from callobuzz_rag.cob.tools.registry import register
from callobuzz_rag.cob.types import CobRole
from callobuzz_rag.core.schemas import StrictRequest

PENDING_LINK = "/admin/customers?status=pending"


class CustomersArgs(StrictRequest):
    pass


class CustomersTool(ReadTool):
    name: ClassVar[str] = "customers"
    description: ClassVar[str] = (
        "Operator only: how many customer workspaces there are by status, which are waiting "
        "for approval, and each one's plan."
    )
    args_model: ClassVar[type[StrictRequest]] = CustomersArgs
    roles: ClassVar[frozenset[CobRole]] = frozenset({CobRole.ADMIN})
    routes: ClassVar[tuple[RouteCall, ...]] = (RouteCall("GET", "/api/v1/admin/tenants"),)
    screen: ClassVar[str] = PENDING_LINK

    async def answer(self, ctx: ToolContext, args: Any) -> ToolResult:
        lang = ctx.language
        body = await fetch(ctx.client, "/api/v1/admin/tenants", {"limit": 100})
        items = [
            t
            for t in ((body or {}).get("items") or [])
            if isinstance(t, dict) and not t.get("is_operator")
        ]
        link = link_to(self, ctx, PENDING_LINK)
        if not items:
            return ToolResult(markdown=ts("customers_none", lang), link=link, data={"total": 0})
        counts = Counter(str(t.get("status") or "?") for t in items)
        pending = sorted(
            (t for t in items if t.get("status") == "pending"),
            key=lambda t: str(t.get("created_at") or ""),
            reverse=True,
        )
        statuses = ", ".join(f"{status} {num(n)}" for status, n in sorted(counts.items()))
        sentence = ts("customers_sentence", lang, n=num(len(items)), statuses=esc(statuses, 100))
        if pending:
            names = ", ".join(f"**{esc(t.get('name'), 40)}**" for t in pending[:10])
            sentence += " " + ts("customers_pending", lang, n=num(len(pending)), names=names)
        ranked = pending + [t for t in items if t.get("status") != "pending"]
        rows = [
            [
                t.get("name"),
                t.get("status"),
                t.get("plan_name") or "-",
                num(t.get("questions_this_month")),
            ]
            for t in ranked
        ]
        headers = [
            ts("h_customer", lang),
            ts("h_status", lang),
            ts("h_plan", lang),
            ts("h_questions", lang),
        ]
        total = int((body or {}).get("total", len(items)))
        return ToolResult(
            markdown=f"{sentence}\n\n{table(headers, rows, more=max(0, total - len(rows)), language=lang)}",
            link=link,
            data={"total": total, "pending": len(pending)},
        )


register(CustomersTool())
