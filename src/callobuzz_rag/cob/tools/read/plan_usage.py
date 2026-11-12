"""plan_usage: the plan, what has been used of each limit, and when the month resets."""

from typing import Any, ClassVar

from callobuzz_rag.cob.tools.base import ReadTool, RouteCall, ToolContext, ToolResult
from callobuzz_rag.cob.tools.cards import esc, kv, link_to, num, ts, used_of, when
from callobuzz_rag.cob.tools.http import ToolFailure, fetch
from callobuzz_rag.cob.tools.registry import register
from callobuzz_rag.cob.types import CobRole
from callobuzz_rag.core.schemas import StrictRequest


class PlanUsageArgs(StrictRequest):
    pass


class PlanUsageTool(ReadTool):
    name: ClassVar[str] = "plan_usage"
    description: ClassVar[str] = (
        "The workspace's plan and how much of each limit is used: questions this month, "
        "documents, knowledge bases, widget keys, and when the month resets."
    )
    args_model: ClassVar[type[StrictRequest]] = PlanUsageArgs
    roles: ClassVar[frozenset[CobRole]] = frozenset(
        {CobRole.OWNER, CobRole.MEMBER, CobRole.OPERATOR_OWNER}
    )
    routes: ClassVar[tuple[RouteCall, ...]] = (
        RouteCall("GET", "/api/v1/tenants/me/plan"),
        RouteCall("GET", "/api/v1/usage/consumption"),
    )
    screen: ClassVar[str] = "/plan"

    async def answer(self, ctx: ToolContext, args: Any) -> ToolResult:
        body = await fetch(ctx.client, "/api/v1/tenants/me/plan")
        plan = (body or {}).get("plan") or {}
        limits = (body or {}).get("limits") or plan
        usage = (body or {}).get("usage") or {}
        try:
            consumption = await fetch(ctx.client, "/api/v1/usage/consumption") or {}
        except ToolFailure:
            consumption = {}
        questions = usage.get("questions", consumption.get("questions"))
        question_limit = limits.get("monthly_questions", consumption.get("question_limit"))
        lang = ctx.language
        sentence = ts(
            "plan_sentence",
            lang,
            plan=esc(plan.get("name") or "-", 60),
            used=used_of(questions, question_limit, lang),
        )
        pairs: list[tuple[str, Any]] = [
            (
                ts("kv_documents", lang),
                used_of(usage.get("documents"), limits.get("max_documents"), lang),
            ),
            (
                ts("kv_knowledge_bases", lang),
                used_of(usage.get("knowledge_bases"), limits.get("max_knowledge_bases"), lang),
            ),
            (
                ts("kv_widget_keys", lang),
                used_of(usage.get("widget_keys"), limits.get("max_widget_keys"), lang),
            ),
            (ts("kv_resets", lang), when((body or {}).get("resets_at"), lang)),
        ]
        return ToolResult(
            markdown=f"{sentence}\n\n{kv(pairs)}",
            link=link_to(self, ctx, self.screen),
            data={"questions": questions, "limit": question_limit, "used": num(questions)},
        )


register(PlanUsageTool())
