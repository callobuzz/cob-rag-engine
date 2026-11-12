"""retry_ingestion, delete_knowledge_base and delete_document: what a knowledge base holds."""

import uuid
from typing import Any, ClassVar

from callobuzz_rag.cob.tools.actions.common import (
    ActionBase,
    ActionOutcome,
    ProposalDraft,
    ask,
    one_kb,
    pick_by_name,
)
from callobuzz_rag.cob.tools.base import RouteCall, ToolContext, ToolResult
from callobuzz_rag.cob.tools.cards import esc, num, ts
from callobuzz_rag.cob.tools.http import ToolFailure, fetch
from callobuzz_rag.cob.tools.registry import register
from callobuzz_rag.cob.tools.resolve import list_kbs
from callobuzz_rag.cob.types import CobRole
from callobuzz_rag.conversation.schema import QuickReply
from callobuzz_rag.core.schemas import StrictRequest

LIST_KBS = RouteCall("GET", "/api/v1/corpora")
OWNERS = frozenset({CobRole.OWNER, CobRole.OPERATOR_OWNER})
MAX_KBS = 10


# ---------------------------------------------------------------- retry_ingestion


class RetryIngestionArgs(StrictRequest):
    kb: str | None = None
    file: str | None = None


class RetryIngestionExec(StrictRequest):
    job_id: uuid.UUID
    file_name: str
    corpus_id: uuid.UUID | None = None


class RetryIngestionTool(ActionBase):
    name: ClassVar[str] = "retry_ingestion"
    description: ClassVar[str] = (
        "Run a failed upload again from its stored file (args.file is the file name, args.kb "
        "the knowledge base)."
    )
    args_model: ClassVar[type[StrictRequest]] = RetryIngestionArgs
    exec_model: ClassVar[type[StrictRequest]] = RetryIngestionExec
    roles: ClassVar[frozenset[CobRole]] = frozenset(
        {CobRole.OWNER, CobRole.MEMBER, CobRole.OPERATOR_OWNER}
    )
    routes: ClassVar[tuple[RouteCall, ...]] = (
        RouteCall("POST", "/api/v1/ingest/jobs/{job_id}/retry"),
        RouteCall("GET", "/api/v1/ingest/jobs"),
        LIST_KBS,
    )
    risk: ClassVar[Any] = "low"
    screen: ClassVar[str] = "/documents"

    async def propose(self, ctx: ToolContext, args: Any) -> ProposalDraft | ToolResult:
        lang = ctx.language
        params: dict[str, Any] = {"status": "failed", "limit": 50}
        kb_name: str | None = None
        if args.kb:
            kb = await one_kb(ctx, args.kb)
            if isinstance(kb, ToolResult):
                return kb
            params["corpus_id"] = str(kb.get("id"))
            kb_name = str(kb.get("name"))
        try:
            body = await fetch(ctx.client, "/api/v1/ingest/jobs", params)
        except ToolFailure:
            return ToolResult(markdown=ts("unavailable", lang), ok=False)
        jobs = [
            j
            for j in ((body or {}).get("items") or [])
            if isinstance(j, dict)
            and j.get("mode") != "records"
            and j.get("file_name")
            and not j.get("retried")
        ]
        if not jobs:
            return ToolResult(markdown=ts("no_failed", lang), link=self.link(ctx))
        chosen = pick_by_name(jobs, args.file, ctx.question, "file_name")
        if not chosen:
            if args.file:
                return ToolResult(
                    markdown=ts("not_found", lang, name=f"**{esc(args.file, 60)}**"), ok=False
                )
            chosen = jobs if len(jobs) == 1 else []
        if len(chosen) != 1:
            names = ", ".join(f"**{esc(j.get('file_name'), 40)}**" for j in jobs[:5])
            replies = [
                QuickReply(
                    label=str(j.get("file_name"))[:20] or "?",
                    message=f"Retry {j.get('file_name')}"[:200],
                )
                for j in jobs[:5]
            ]
            return ToolResult(markdown=ts("ask_file", lang, names=names), quick_replies=replies)
        job = chosen[0]
        file_name = str(job.get("file_name"))
        pairs = [(ts("a_file", lang), file_name)]
        if kb_name:
            pairs.insert(0, (ts("a_kb", lang), kb_name))
        return ProposalDraft(
            title=ts("title_retry_ingestion", lang),
            summary=ts("sum_retry_ingestion", lang, file=esc(file_name, 80)),
            args=pairs,
            risk="low",
            exec_args={
                "job_id": str(job.get("id")),
                "file_name": file_name,
                "corpus_id": str(job["corpus_id"]) if job.get("corpus_id") else None,
            },
        )

    async def execute(self, ctx: ToolContext, exec_args: dict[str, Any]) -> ActionOutcome:
        response = await ctx.client.request(
            "POST", f"/api/v1/ingest/jobs/{exec_args['job_id']}/retry"
        )
        corpus = exec_args.get("corpus_id")
        return self.done(
            ctx,
            response,
            "POST",
            "/api/v1/ingest/jobs/{job_id}/retry",
            ts("done_retry_ingestion", ctx.language, file=esc(exec_args["file_name"], 80)),
            link=f"/documents?kb={corpus}" if corpus else None,
            result={"job_id": exec_args["job_id"]},
        )


# ------------------------------------------------------------ delete_knowledge_base


class DeleteKnowledgeBaseArgs(StrictRequest):
    kb: str | None = None


class DeleteKnowledgeBaseExec(StrictRequest):
    corpus_id: uuid.UUID
    kb_name: str


class DeleteKnowledgeBaseTool(ActionBase):
    name: ClassVar[str] = "delete_knowledge_base"
    description: ClassVar[str] = (
        "Delete a knowledge base with everything in it, for good (args.kb)."
    )
    args_model: ClassVar[type[StrictRequest]] = DeleteKnowledgeBaseArgs
    exec_model: ClassVar[type[StrictRequest]] = DeleteKnowledgeBaseExec
    roles: ClassVar[frozenset[CobRole]] = OWNERS
    routes: ClassVar[tuple[RouteCall, ...]] = (
        RouteCall("DELETE", "/api/v1/corpora/{corpus_id}"),
        RouteCall("GET", "/api/v1/corpora/{corpus_id}/delete-preview"),
        LIST_KBS,
    )
    destructive: ClassVar[bool] = True
    owner_only: ClassVar[bool] = True
    risk: ClassVar[Any] = "high"
    screen: ClassVar[str] = "/kbs"

    async def propose(self, ctx: ToolContext, args: Any) -> ProposalDraft | ToolResult:
        lang = ctx.language
        # A deletion is never guessed from the screen the person is on: the name must be theirs.
        if not args.kb and not await _named_in_question(ctx):
            return ask(ctx, "ask_which_kb_delete")
        kb = await one_kb(ctx, args.kb)
        if isinstance(kb, ToolResult):
            return kb
        try:
            preview = await fetch(ctx.client, f"/api/v1/corpora/{kb.get('id')}/delete-preview")
        except ToolFailure:
            return ToolResult(markdown=ts("unavailable", lang), ok=False)
        name = str((preview or {}).get("confirm_name") or kb.get("name"))
        counts = (preview or {}).get("counts") or {}
        return ProposalDraft(
            title=ts("title_delete_knowledge_base", lang),
            summary=ts(
                "sum_delete_knowledge_base",
                lang,
                kb=esc(name, 80),
                docs=num(counts.get("documents", 0)),
            ),
            args=[
                (ts("a_kb", lang), name),
                (ts("a_documents", lang), num(counts.get("documents", 0))),
            ],
            risk="high",
            confirm_name=name,
            exec_args={"corpus_id": str(kb.get("id")), "kb_name": name},
        )

    async def current_name(self, ctx: ToolContext, exec_args: dict[str, Any]) -> str:
        preview = await fetch(
            ctx.client, f"/api/v1/corpora/{exec_args['corpus_id']}/delete-preview"
        )
        return str((preview or {}).get("confirm_name") or "")

    async def execute(self, ctx: ToolContext, exec_args: dict[str, Any]) -> ActionOutcome:
        response = await ctx.client.request(
            "DELETE",
            f"/api/v1/corpora/{exec_args['corpus_id']}",
            params={"confirm_name": exec_args.get("confirm_name")},
        )
        return self.done(
            ctx,
            response,
            "DELETE",
            "/api/v1/corpora/{corpus_id}",
            ts("done_delete_knowledge_base", ctx.language, kb=esc(exec_args["kb_name"], 80)),
            result={"corpus_id": exec_args["corpus_id"]},
        )


async def _named_in_question(ctx: ToolContext) -> bool:
    from callobuzz_rag.cob.tools.resolve import named_in

    try:
        return bool(named_in(ctx.question, await list_kbs(ctx)))
    except ToolFailure:
        return False


# ------------------------------------------------------------------ delete_document


class DeleteDocumentArgs(StrictRequest):
    kb: str | None = None
    file: str | None = None


class DeleteDocumentExec(StrictRequest):
    document_id: uuid.UUID
    corpus_id: uuid.UUID
    file_name: str
    kb_name: str


class DeleteDocumentTool(ActionBase):
    name: ClassVar[str] = "delete_document"
    description: ClassVar[str] = (
        "Delete one document from a knowledge base, for good (args.file is its file name, "
        "args.kb the knowledge base)."
    )
    args_model: ClassVar[type[StrictRequest]] = DeleteDocumentArgs
    exec_model: ClassVar[type[StrictRequest]] = DeleteDocumentExec
    roles: ClassVar[frozenset[CobRole]] = OWNERS
    routes: ClassVar[tuple[RouteCall, ...]] = (
        RouteCall("DELETE", "/api/v1/documents/{document_id}"),
        RouteCall("GET", "/api/v1/documents"),
        RouteCall("GET", "/api/v1/documents/{document_id}/delete-preview"),
        LIST_KBS,
    )
    destructive: ClassVar[bool] = True
    owner_only: ClassVar[bool] = True
    risk: ClassVar[Any] = "high"
    screen: ClassVar[str] = "/documents"

    async def propose(self, ctx: ToolContext, args: Any) -> ProposalDraft | ToolResult:
        lang = ctx.language
        if not args.file:
            return ask(ctx, "ask_document")
        try:
            kbs = await list_kbs(ctx)
        except ToolFailure:
            return ToolResult(markdown=ts("unavailable", lang), ok=False)
        if args.kb:
            kb = await one_kb(ctx, args.kb)
            if isinstance(kb, ToolResult):
                return kb
            kbs = [kb]
        hits: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for kb in kbs[:MAX_KBS]:
            try:
                body = await fetch(
                    ctx.client,
                    "/api/v1/documents",
                    {"corpus_id": str(kb.get("id")), "search": args.file, "limit": 20},
                )
            except ToolFailure:
                continue
            for doc in (body or {}).get("items") or []:
                if isinstance(doc, dict):
                    hits.append((kb, doc))
        exact = [
            h for h in hits if str(h[1].get("file_name") or "").casefold() == args.file.casefold()
        ]
        hits = exact or hits
        if not hits:
            return ToolResult(
                markdown=ts("not_found", lang, name=f"**{esc(args.file, 60)}**"), ok=False
            )
        if len(hits) > 1:
            names = ", ".join(
                f"**{esc(d.get('file_name') or d.get('external_id'), 40)}** ({esc(k.get('name'), 30)})"
                for k, d in hits[:5]
            )
            return ToolResult(markdown=ts("ask_document_pick", lang, names=names))
        kb, doc = hits[0]
        try:
            preview = await fetch(
                ctx.client,
                f"/api/v1/documents/{doc.get('id')}/delete-preview",
                {"corpus_id": str(kb.get("id"))},
            )
        except ToolFailure:
            return ToolResult(markdown=ts("unavailable", lang), ok=False)
        name = str((preview or {}).get("confirm_name") or doc.get("file_name") or "")
        return ProposalDraft(
            title=ts("title_delete_document", lang),
            summary=ts("sum_delete_document", lang, file=esc(name, 80), kb=esc(kb.get("name"), 60)),
            args=[(ts("a_file", lang), name), (ts("a_kb", lang), str(kb.get("name")))],
            risk="high",
            confirm_name=name,
            exec_args={
                "document_id": str(doc.get("id")),
                "corpus_id": str(kb.get("id")),
                "file_name": name,
                "kb_name": str(kb.get("name")),
            },
        )

    async def current_name(self, ctx: ToolContext, exec_args: dict[str, Any]) -> str:
        preview = await fetch(
            ctx.client,
            f"/api/v1/documents/{exec_args['document_id']}/delete-preview",
            {"corpus_id": exec_args["corpus_id"]},
        )
        return str((preview or {}).get("confirm_name") or "")

    async def execute(self, ctx: ToolContext, exec_args: dict[str, Any]) -> ActionOutcome:
        response = await ctx.client.request(
            "DELETE",
            f"/api/v1/documents/{exec_args['document_id']}",
            params={
                "corpus_id": exec_args["corpus_id"],
                "confirm_name": exec_args.get("confirm_name"),
            },
        )
        return self.done(
            ctx,
            response,
            "DELETE",
            "/api/v1/documents/{document_id}",
            ts("done_delete_document", ctx.language, file=esc(exec_args["file_name"], 80)),
            link=f"/documents?kb={exec_args['corpus_id']}",
            result={"document_id": exec_args["document_id"]},
        )


register(RetryIngestionTool())
register(DeleteKnowledgeBaseTool())
register(DeleteDocumentTool())
