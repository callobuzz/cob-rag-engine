"""assistant_status: is each knowledge base's assistant on, what is published, what is pending."""

from typing import Any, ClassVar

from callobuzz_rag.cob.tools.base import ReadTool, RouteCall, ToolContext, ToolResult
from callobuzz_rag.cob.tools.cards import esc, kv, link_to, table, ts, when
from callobuzz_rag.cob.tools.http import ToolFailure, fetch
from callobuzz_rag.cob.tools.registry import register
from callobuzz_rag.cob.tools.resolve import Ambiguous, NotFound, clarify, list_kbs, pick_kbs
from callobuzz_rag.cob.types import CobRole
from callobuzz_rag.core.schemas import StrictRequest

MAX_KBS = 10


class AssistantStatusArgs(StrictRequest):
    kb: str | None = None


def _config(state: dict[str, Any]) -> dict[str, Any]:
    for slot in ("published", "draft"):
        version = state.get(slot)
        if isinstance(version, dict) and isinstance(version.get("config"), dict):
            return dict(version["config"])
    return {}


def _flag(config: dict[str, Any], section: str, language: str) -> str:
    block = config.get(section)
    on = isinstance(block, dict) and bool(block.get("enabled"))
    return ts("on" if on else "off", language)


class AssistantStatusTool(ReadTool):
    name: ClassVar[str] = "assistant_status"
    description: ClassVar[str] = (
        "Whether a knowledge base's assistant is on, which version is published and when, "
        "whether a draft is waiting, its languages, and whether leads and handoff are on."
    )
    args_model: ClassVar[type[StrictRequest]] = AssistantStatusArgs
    roles: ClassVar[frozenset[CobRole]] = frozenset(
        {CobRole.OWNER, CobRole.MEMBER, CobRole.OPERATOR_OWNER, CobRole.ADMIN}
    )
    routes: ClassVar[tuple[RouteCall, ...]] = (
        RouteCall("GET", "/api/v1/corpora"),
        RouteCall("GET", "/api/v1/corpora/{corpus_id}/assistant"),
    )
    screen: ClassVar[str] = "/kbs"

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
        states: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for kb in picked[:MAX_KBS]:
            state = await fetch(ctx.client, f"/api/v1/corpora/{kb.get('id')}/assistant")
            states.append((kb, state if isinstance(state, dict) else {}))
        if len(states) == 1:
            kb, state = states[0]
            return self._one(ctx, kb, state)
        headers = [
            ts("h_knowledge_base", lang),
            ts("h_assistant", lang),
            ts("h_published", lang),
            ts("h_draft", lang),
        ]
        rows = [
            [
                kb.get("name"),
                self._enabled(state, lang),
                self._published(state, lang),
                self._draft(state, lang),
            ]
            for kb, state in states
        ]
        on = sum(1 for _kb, s in states if s.get("enabled"))
        sentence = ts("assistant_sentence_all", lang, on=on, n=len(states))
        return ToolResult(
            markdown=f"{sentence}\n\n{table(headers, rows, language=lang)}",
            link=link_to(self, ctx, self.screen),
            data={"on": on, "knowledge_bases": len(states)},
        )

    def _enabled(self, state: dict[str, Any], lang: str) -> str:
        return ts("on" if state.get("enabled") else "off", lang)

    def _published(self, state: dict[str, Any], lang: str) -> str:
        published = state.get("published")
        if not isinstance(published, dict):
            return ts("none", lang)
        return ts(
            "version_on",
            lang,
            version=published.get("version") or "-",
            when=when(published.get("published_at"), lang),
        )

    def _draft(self, state: dict[str, Any], lang: str) -> str:
        return ts("yes" if isinstance(state.get("draft"), dict) else "no", lang)

    def _one(self, ctx: ToolContext, kb: dict[str, Any], state: dict[str, Any]) -> ToolResult:
        lang = ctx.language
        config = _config(state)
        languages = (config.get("language") or {}).get("supported") if config else None
        sentence = ts(
            "assistant_sentence_one",
            lang,
            kb=esc(kb.get("name"), 60),
            state=ts("on" if state.get("enabled") else "off", lang).lower(),
        )
        pairs: list[tuple[str, Any]] = [
            (ts("h_published", lang), self._published(state, lang)),
            (ts("kv_draft_pending", lang), self._draft(state, lang)),
            (ts("h_languages", lang), ", ".join(languages) if languages else "-"),
            (ts("h_leads", lang), _flag(config, "leads", lang)),
            (ts("h_handoff", lang), _flag(config, "handoff", lang)),
        ]
        return ToolResult(
            markdown=f"{sentence}\n\n{kv(pairs)}",
            link=link_to(self, ctx, f"/kbs/{kb.get('id')}?tab=assistant"),
            data={"enabled": bool(state.get("enabled"))},
        )


register(AssistantStatusTool())
