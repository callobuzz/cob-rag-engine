"""documents: how many documents each knowledge base holds, the last ingest, and failures."""

from typing import Any, ClassVar

from callobuzz_rag.cob.tools.base import ReadTool, RouteCall, ToolContext, ToolResult
from callobuzz_rag.cob.tools.cards import esc, kv, link_to, num, table, ts, when
from callobuzz_rag.cob.tools.http import ToolFailure, fetch
from callobuzz_rag.cob.tools.read.job_failure import explain
from callobuzz_rag.cob.tools.registry import register
from callobuzz_rag.cob.tools.resolve import Ambiguous, NotFound, clarify, list_kbs, pick_kbs
from callobuzz_rag.cob.types import CobRole
from callobuzz_rag.core.schemas import StrictRequest

MAX_FAILURES = 3


class DocumentsArgs(StrictRequest):
    kb: str | None = None


class DocumentsTool(ReadTool):
    name: ClassVar[str] = "documents"
    description: ClassVar[str] = (
        "How many documents each knowledge base has, when something was last added, and any "
        "uploads that failed (name a knowledge base in args.kb to focus on one)."
    )
    args_model: ClassVar[type[StrictRequest]] = DocumentsArgs
    roles: ClassVar[frozenset[CobRole]] = frozenset(
        {CobRole.OWNER, CobRole.MEMBER, CobRole.OPERATOR_OWNER, CobRole.ADMIN}
    )
    routes: ClassVar[tuple[RouteCall, ...]] = (
        RouteCall("GET", "/api/v1/corpora"),
        RouteCall("GET", "/api/v1/documents"),
        RouteCall("GET", "/api/v1/ingest/jobs"),
    )
    screen: ClassVar[str] = "/documents"

    async def answer(self, ctx: ToolContext, args: Any) -> ToolResult:
        lang = ctx.language
        kbs = await list_kbs(ctx)
        if not kbs:
            return ToolResult(markdown=ts("kbs_none", lang), link=link_to(self, ctx, self.screen))
        picked = await pick_kbs(ctx, args.kb, kbs)
        if isinstance(picked, NotFound):
            raise ToolFailure("not_found", f"**{esc(picked.name, 60)}**")
        if isinstance(picked, Ambiguous):
            return clarify(ctx, picked.candidates)
        if len(picked) == 1:
            return await self._one(ctx, picked[0])
        rows = [
            [k.get("name"), num(k.get("document_count")), when(k.get("last_activity_at"), lang)]
            for k in picked
        ]
        total = sum(int(k.get("document_count") or 0) for k in picked)
        sentence = ts("docs_sentence_all", lang, n=num(total), kbs=num(len(picked)))
        headers = [
            ts("h_knowledge_base", lang),
            ts("h_documents", lang),
            ts("h_last_activity", lang),
        ]
        return ToolResult(
            markdown=f"{sentence}\n\n{table(headers, rows, language=lang)}",
            link=link_to(self, ctx, self.screen),
            data={"documents": total},
        )

    async def _one(self, ctx: ToolContext, kb: dict[str, Any]) -> ToolResult:
        lang = ctx.language
        corpus_id = str(kb.get("id"))
        recent = await fetch(
            ctx.client, "/api/v1/documents", {"corpus_id": corpus_id, "limit": 100}
        )
        latest = max(
            (str(d.get("ingested_at") or "") for d in ((recent or {}).get("items") or [])),
            default="",
        )
        jobs = await fetch(
            ctx.client,
            "/api/v1/ingest/jobs",
            {"corpus_id": corpus_id, "status": "failed", "limit": MAX_FAILURES},
        )
        failed = [j for j in ((jobs or {}).get("items") or []) if isinstance(j, dict)]
        failed_total = int((jobs or {}).get("total", len(failed)))
        sentence = ts(
            "docs_sentence_one", lang, kb=esc(kb.get("name"), 60), n=num(kb.get("document_count"))
        )
        pairs: list[tuple[str, Any]] = [
            (ts("kv_last_added", lang), when(latest or kb.get("last_activity_at"), lang)),
            (ts("kv_failed_uploads", lang), num(failed_total)),
        ]
        text = f"{sentence}\n\n{kv(pairs)}"
        if failed:
            rows = [
                [
                    j.get("file_name") or "-",
                    when(j.get("finished_at") or j.get("created_at"), lang),
                    explain(j.get("errors"), lang)[0],
                ]
                for j in failed
            ]
            headers = [ts("h_file", lang), ts("h_when", lang), ts("h_reason", lang)]
            text += "\n\n" + table(
                headers, rows, more=max(0, failed_total - len(rows)), language=lang
            )
        return ToolResult(
            markdown=text,
            link=link_to(self, ctx, f"/documents?kb={corpus_id}"),
            data={"kb": corpus_id, "failed": failed_total},
        )


register(DocumentsTool())
