"""leads_handoffs: how many leads and handoff requests there are. Counts only.

A lead's name, phone, e-mail and message are never read into a card: the Leads screen is where
they are kept (encrypted), and COB's replies are stored in the chat history."""

from datetime import date
from typing import Any, ClassVar

from callobuzz_rag.cob.tools.base import ReadTool, RouteCall, ToolContext, ToolResult
from callobuzz_rag.cob.tools.cards import link_to, num, table, ts, when
from callobuzz_rag.cob.tools.http import fetch
from callobuzz_rag.cob.tools.registry import register
from callobuzz_rag.cob.types import CobRole
from callobuzz_rag.core.schemas import StrictRequest

STATUSES = ("contacted", "lost", "new", "spam", "won")  # api/routes/leads.py _STATUS
PAGE = 100


class LeadsArgs(StrictRequest):
    pass


class LeadsHandoffsTool(ReadTool):
    name: ClassVar[str] = "leads_handoffs"
    description: ClassVar[str] = (
        "How many leads and handoff requests the assistants collected, this month and in "
        "total, by status. Counts only, never contact details."
    )
    args_model: ClassVar[type[StrictRequest]] = LeadsArgs
    roles: ClassVar[frozenset[CobRole]] = frozenset(
        {CobRole.OWNER, CobRole.MEMBER, CobRole.OPERATOR_OWNER}
    )
    routes: ClassVar[tuple[RouteCall, ...]] = (
        RouteCall("GET", "/api/v1/leads"),
        RouteCall("GET", "/api/v1/handoffs"),
    )
    screen: ClassVar[str] = "/leads"

    async def answer(self, ctx: ToolContext, args: Any) -> ToolResult:
        lang = ctx.language
        month_start = date(ctx.now.year, ctx.now.month, 1).isoformat()
        leads_all = await fetch(ctx.client, "/api/v1/leads", {"limit": 1})
        leads_month = await fetch(
            ctx.client, "/api/v1/leads", {"limit": 1, "date_from": month_start}
        )
        by_status = await _status_counts(ctx, month_start)
        hand_all = await fetch(ctx.client, "/api/v1/handoffs", {"limit": 1})
        hand_month = await fetch(
            ctx.client, "/api/v1/handoffs", {"limit": PAGE, "from": month_start}
        )
        counts = {
            "leads": (_total(leads_month), _total(leads_all), _newest(leads_all, leads_month)),
            "handoffs": (_total(hand_month), _total(hand_all), _newest(hand_all, hand_month)),
        }
        headers = [
            "",
            ts("h_this_month", lang),
            ts("h_all_time", lang),
            ts("h_newest", lang),
        ]
        rows = [
            [
                ts("h_leads", lang),
                num(counts["leads"][0]),
                num(counts["leads"][1]),
                when(counts["leads"][2], lang),
            ],
            [
                ts("h_handoffs", lang),
                num(counts["handoffs"][0]),
                num(counts["handoffs"][1]),
                when(counts["handoffs"][2], lang),
            ],
        ]
        sentence = ts(
            "leads_sentence",
            lang,
            leads=num(counts["leads"][0]),
            handoffs=num(counts["handoffs"][0]),
        )
        text = f"{sentence}\n\n{table(headers, rows, language=lang)}"
        if by_status:
            text += "\n\n" + ts("leads_by_status", lang, statuses=by_status)
        return ToolResult(
            markdown=text,
            link=link_to(self, ctx, self.screen),
            data={"leads_month": counts["leads"][0], "leads_all": counts["leads"][1]},
        )


def _items(body: Any) -> list[dict[str, Any]]:
    items = body.get("items") if isinstance(body, dict) else None
    return [i for i in (items or []) if isinstance(i, dict)]


def _total(body: Any) -> int:
    if isinstance(body, dict) and body.get("total") is not None:
        return int(body["total"])
    return len(_items(body))


def _newest(*bodies: Any) -> str | None:
    dates = [str(i.get("created_at")) for b in bodies for i in _items(b) if i.get("created_at")]
    return max(dates) if dates else None


async def _status_counts(ctx: ToolContext, month_start: str) -> str:
    """This month's leads per status, each from the route's own total (not a page of rows)."""
    parts: list[str] = []
    for status in STATUSES:
        body = await fetch(
            ctx.client, "/api/v1/leads", {"limit": 1, "date_from": month_start, "status": status}
        )
        n = _total(body)
        if n:
            parts.append(f"{status} {n}")
    return ", ".join(parts)


register(LeadsHandoffsTool())
