"""knowledge_bases: the workspace's knowledge bases with their sizes."""

from typing import Any, ClassVar

from callobuzz_rag.cob.tools.base import ReadTool, RouteCall, ToolContext, ToolResult
from callobuzz_rag.cob.tools.cards import link_to, num, table, ts, when
from callobuzz_rag.cob.tools.http import fetch
from callobuzz_rag.cob.tools.registry import register
from callobuzz_rag.cob.types import CobRole
from callobuzz_rag.core.schemas import StrictRequest


class KnowledgeBasesArgs(StrictRequest):
    pass


class KnowledgeBasesTool(ReadTool):
    name: ClassVar[str] = "knowledge_bases"
    description: ClassVar[str] = (
        "The knowledge bases the person can see: name, how many documents, when created."
    )
    args_model: ClassVar[type[StrictRequest]] = KnowledgeBasesArgs
    roles: ClassVar[frozenset[CobRole]] = frozenset(
        {CobRole.OWNER, CobRole.MEMBER, CobRole.OPERATOR_OWNER, CobRole.ADMIN}
    )
    routes: ClassVar[tuple[RouteCall, ...]] = (RouteCall("GET", "/api/v1/corpora"),)
    screen: ClassVar[str] = "/kbs"

    async def answer(self, ctx: ToolContext, args: Any) -> ToolResult:
        body = await fetch(ctx.client, "/api/v1/corpora", {"limit": 100})
        items = [k for k in ((body or {}).get("items") or []) if isinstance(k, dict)]
        total = int((body or {}).get("total", len(items)))
        lang = ctx.language
        if not items:
            return ToolResult(
                markdown=ts("kbs_none", lang),
                link=link_to(self, ctx, self.screen),
                data={"total": 0},
            )
        admin = ctx.caller.role == CobRole.ADMIN
        headers = [ts("h_name", lang), ts("h_documents", lang), ts("h_created", lang)]
        if admin:
            headers.insert(1, ts("h_workspace", lang))
        rows = []
        for kb in items:
            row = [kb.get("name"), num(kb.get("document_count")), when(kb.get("created_at"), lang)]
            if admin:
                row.insert(1, kb.get("tenant_name") or "")
            rows.append(row)
        sentence = ts("kbs_sentence", lang, n=num(total))
        return ToolResult(
            markdown=f"{sentence}\n\n{table(headers, rows, more=max(0, total - len(items)), language=lang)}",
            link=link_to(self, ctx, self.screen),
            data={"total": total},
        )


register(KnowledgeBasesTool())
