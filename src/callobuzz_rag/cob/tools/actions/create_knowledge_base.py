"""create_knowledge_base: a new, empty knowledge base in the workspace."""

import re
from typing import Any, ClassVar

from callobuzz_rag.cob.tools.actions.common import ActionBase, ActionOutcome, ProposalDraft, ask
from callobuzz_rag.cob.tools.base import RouteCall, ToolContext, ToolResult
from callobuzz_rag.cob.tools.cards import esc, ts
from callobuzz_rag.cob.tools.registry import register
from callobuzz_rag.cob.types import CobRole
from callobuzz_rag.core.schemas import StrictRequest

# curly double quotes (U+201C, U+201D) and curly single quotes (U+2018, U+2019), by code point
_OPEN = '"' + chr(0x201C) + chr(0x2018) + "'"
_CLOSE = '"' + chr(0x201D) + chr(0x2019) + "'"
_NAMED = re.compile(
    rf"(?:called|named|name it|naam(?: se)?|नाम)\s*[:\-]?\s*[{_OPEN}]?"
    rf"([^{_CLOSE}\n.?!]{{2,80}})",
    re.I,
)
_QUOTED = re.compile(rf"[{_OPEN}]([^{_CLOSE}]{{2,80}})[{_CLOSE}]")


def name_in(text: str) -> str | None:
    """A name the person gave in their own words: in quotes, or after "called"."""
    for rx in (_QUOTED, _NAMED):
        match = rx.search(text or "")
        if match:
            name = " ".join(match.group(1).split()).strip(" .,;")
            if name:
                return name[:80]
    return None


class CreateKnowledgeBaseArgs(StrictRequest):
    name: str | None = None


class CreateKnowledgeBaseExec(StrictRequest):
    name: str


class CreateKnowledgeBaseTool(ActionBase):
    name: ClassVar[str] = "create_knowledge_base"
    description: ClassVar[str] = (
        "Create a new, empty knowledge base (args.name is what to call it)."
    )
    args_model: ClassVar[type[StrictRequest]] = CreateKnowledgeBaseArgs
    exec_model: ClassVar[type[StrictRequest]] = CreateKnowledgeBaseExec
    roles: ClassVar[frozenset[CobRole]] = frozenset({CobRole.OWNER, CobRole.OPERATOR_OWNER})
    routes: ClassVar[tuple[RouteCall, ...]] = (RouteCall("POST", "/api/v1/corpora"),)
    owner_only: ClassVar[bool] = True
    risk: ClassVar[Any] = "low"
    screen: ClassVar[str] = "/kbs"

    async def propose(self, ctx: ToolContext, args: Any) -> ProposalDraft | ToolResult:
        lang = ctx.language
        name = (args.name or name_in(ctx.question) or "").strip()
        if not name:
            return ask(ctx, "ask_kb_name")
        return ProposalDraft(
            title=ts("title_create_knowledge_base", lang),
            summary=ts("sum_create_knowledge_base", lang, name=esc(name, 80)),
            args=[(ts("a_name", lang), name)],
            risk="low",
            exec_args={"name": name},
        )

    async def execute(self, ctx: ToolContext, exec_args: dict[str, Any]) -> ActionOutcome:
        # The response carries the knowledge base's own key; it is deliberately not read.
        response = await ctx.client.request(
            "POST", "/api/v1/corpora", json={"name": exec_args["name"]}
        )
        body = response.body if isinstance(response.body, dict) else {}
        corpus_id = body.get("id")
        return self.done(
            ctx,
            response,
            "POST",
            "/api/v1/corpora",
            ts("done_create_knowledge_base", ctx.language, name=esc(exec_args["name"], 80)),
            link=f"/kbs/{corpus_id}" if corpus_id else None,
            result={"corpus_id": str(corpus_id)} if corpus_id else {},
        )


register(CreateKnowledgeBaseTool())
