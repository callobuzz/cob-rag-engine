"""The assistant of a knowledge base: switch it on or off, publish its draft, change the language
it replies in, and open guided setup (a link, nothing to confirm)."""

import re
import uuid
from typing import Any, ClassVar

from callobuzz_rag.cob.tools.actions.common import (
    ActionBase,
    ActionOutcome,
    ProposalDraft,
    ask,
    one_kb,
)
from callobuzz_rag.cob.tools.base import RouteCall, ToolContext, ToolResult
from callobuzz_rag.cob.tools.cards import esc, ts
from callobuzz_rag.cob.tools.http import ToolFailure, fetch
from callobuzz_rag.cob.tools.registry import register
from callobuzz_rag.cob.types import CobRole
from callobuzz_rag.core.schemas import StrictRequest

ASSISTANT = "/api/v1/corpora/{corpus_id}/assistant"
ALL_ROLES = frozenset({CobRole.OWNER, CobRole.OPERATOR_OWNER})
LIST_KBS = RouteCall("GET", "/api/v1/corpora")

_OFF = re.compile(
    r"\b(off|disable|deactivate|stop|pause|switch off|turn off|band|bandh)\b|बंद|बन्द", re.I
)
_ON = re.compile(
    r"\b(on|enable|activate|start|resume|switch on|turn on|chalu|shuru)\b|चालू|शुरू", re.I
)
_HINDI = re.compile(r"\bhindi\b|हिंदी|हिन्दी", re.I)
_HINGLISH = re.compile(r"\bhinglish\b|हिंग्लिश|roman hindi", re.I)
_ENGLISH = re.compile(r"\benglish\b|अंग्रेज़ी|अंग्रेजी|angrezi", re.I)


def switch_in(*texts: str | None) -> bool | None:
    """True for on, False for off, None when the person did not say."""
    for text in texts:
        if not text:
            continue
        if _OFF.search(text):
            return False
        if _ON.search(text):
            return True
    return None


def language_in(*texts: str | None) -> str | None:
    for text in texts:
        if not text:
            continue
        if _HINGLISH.search(text):
            return "hi-Latn"
        if _HINDI.search(text):
            return "hi"
        if _ENGLISH.search(text):
            return "en"
    return None


async def assistant_state(ctx: ToolContext, corpus_id: Any) -> dict[str, Any]:
    body = await fetch(ctx.client, f"/api/v1/corpora/{corpus_id}/assistant")
    return body if isinstance(body, dict) else {}


# ------------------------------------------------------------------ toggle_assistant


class ToggleAssistantArgs(StrictRequest):
    kb: str | None = None
    state: str | None = None  # on | off


class ToggleAssistantExec(StrictRequest):
    corpus_id: uuid.UUID
    kb_name: str
    enabled: bool


class ToggleAssistantTool(ActionBase):
    name: ClassVar[str] = "toggle_assistant"
    description: ClassVar[str] = (
        "Switch a knowledge base's assistant on or off (args.kb, args.state is on or off)."
    )
    args_model: ClassVar[type[StrictRequest]] = ToggleAssistantArgs
    exec_model: ClassVar[type[StrictRequest]] = ToggleAssistantExec
    roles: ClassVar[frozenset[CobRole]] = ALL_ROLES
    routes: ClassVar[tuple[RouteCall, ...]] = (
        RouteCall("POST", f"{ASSISTANT}/enabled"),
        LIST_KBS,
    )
    owner_only: ClassVar[bool] = True
    risk: ClassVar[Any] = "medium"
    screen: ClassVar[str] = "/kbs"

    async def propose(self, ctx: ToolContext, args: Any) -> ProposalDraft | ToolResult:
        lang = ctx.language
        enabled = switch_in(args.state, ctx.question)
        kb = await one_kb(ctx, args.kb)
        if isinstance(kb, ToolResult):
            return kb
        if enabled is None:
            return ask(ctx, "ask_toggle", kb=esc(kb.get("name"), 60))
        word = ts("on" if enabled else "off", lang)
        suffix = "on" if enabled else "off"
        return ProposalDraft(
            title=ts(f"title_toggle_assistant_{suffix}", lang),
            summary=ts(f"sum_toggle_assistant_{suffix}", lang, kb=esc(kb.get("name"), 60)),
            args=[(ts("a_kb", lang), str(kb.get("name"))), (ts("a_switch", lang), word)],
            risk="medium",
            exec_args={
                "corpus_id": str(kb.get("id")),
                "kb_name": str(kb.get("name")),
                "enabled": enabled,
            },
        )

    async def execute(self, ctx: ToolContext, exec_args: dict[str, Any]) -> ActionOutcome:
        response = await ctx.client.request(
            "POST",
            f"/api/v1/corpora/{exec_args['corpus_id']}/assistant/enabled",
            json={"enabled": exec_args["enabled"]},
        )
        suffix = "on" if exec_args["enabled"] else "off"
        return self.done(
            ctx,
            response,
            "POST",
            f"{ASSISTANT}/enabled",
            ts(f"done_toggle_assistant_{suffix}", ctx.language, kb=esc(exec_args["kb_name"], 60)),
            link=f"/kbs/{exec_args['corpus_id']}?tab=assistant",
            result={"corpus_id": exec_args["corpus_id"], "enabled": exec_args["enabled"]},
        )


# ----------------------------------------------------------------- publish_assistant


class PublishAssistantArgs(StrictRequest):
    kb: str | None = None


class PublishAssistantExec(StrictRequest):
    corpus_id: uuid.UUID
    kb_name: str
    base_revision: int


class PublishAssistantTool(ActionBase):
    name: ClassVar[str] = "publish_assistant"
    description: ClassVar[str] = "Publish the draft of a knowledge base's assistant (args.kb)."
    args_model: ClassVar[type[StrictRequest]] = PublishAssistantArgs
    exec_model: ClassVar[type[StrictRequest]] = PublishAssistantExec
    roles: ClassVar[frozenset[CobRole]] = ALL_ROLES
    routes: ClassVar[tuple[RouteCall, ...]] = (
        RouteCall("POST", f"{ASSISTANT}/publish"),
        RouteCall("GET", ASSISTANT),
        LIST_KBS,
    )
    owner_only: ClassVar[bool] = True
    risk: ClassVar[Any] = "medium"
    screen: ClassVar[str] = "/kbs"

    async def propose(self, ctx: ToolContext, args: Any) -> ProposalDraft | ToolResult:
        lang = ctx.language
        kb = await one_kb(ctx, args.kb)
        if isinstance(kb, ToolResult):
            return kb
        try:
            state = await assistant_state(ctx, kb.get("id"))
        except ToolFailure:
            return ToolResult(markdown=ts("unavailable", lang), ok=False)
        draft = state.get("draft")
        if not isinstance(draft, dict):
            return ToolResult(
                markdown=ts("no_draft", lang, kb=esc(kb.get("name"), 60)),
                link=self.link(ctx, f"/kbs/{kb.get('id')}?tab=assistant"),
            )
        return ProposalDraft(
            title=ts("title_publish_assistant", lang),
            summary=ts("sum_publish_assistant", lang, kb=esc(kb.get("name"), 60)),
            args=[(ts("a_kb", lang), str(kb.get("name")))],
            risk="medium",
            exec_args={
                "corpus_id": str(kb.get("id")),
                "kb_name": str(kb.get("name")),
                "base_revision": int(draft.get("draft_revision") or 0),
            },
        )

    async def execute(self, ctx: ToolContext, exec_args: dict[str, Any]) -> ActionOutcome:
        response = await ctx.client.request(
            "POST",
            f"/api/v1/corpora/{exec_args['corpus_id']}/assistant/publish",
            json={"base_revision": exec_args["base_revision"]},
        )
        body = response.body if isinstance(response.body, dict) else {}
        return self.done(
            ctx,
            response,
            "POST",
            f"{ASSISTANT}/publish",
            ts(
                "done_publish_assistant",
                ctx.language,
                kb=esc(exec_args["kb_name"], 60),
                version=body.get("version") or "",
            ),
            link=f"/kbs/{exec_args['corpus_id']}?tab=assistant",
            result={"corpus_id": exec_args["corpus_id"]},
        )


# ------------------------------------------------------------ switch_reply_language


class SwitchReplyLanguageArgs(StrictRequest):
    kb: str | None = None
    language: str | None = None  # en | hi | hi-Latn, or the language's name


class SwitchReplyLanguageExec(StrictRequest):
    corpus_id: uuid.UUID
    kb_name: str
    language: str


class SwitchReplyLanguageTool(ActionBase):
    name: ClassVar[str] = "switch_reply_language"
    description: ClassVar[str] = (
        "Make a knowledge base's assistant always reply in one language and publish it "
        "(args.kb, args.language is English, Hindi or Hinglish)."
    )
    args_model: ClassVar[type[StrictRequest]] = SwitchReplyLanguageArgs
    exec_model: ClassVar[type[StrictRequest]] = SwitchReplyLanguageExec
    roles: ClassVar[frozenset[CobRole]] = ALL_ROLES
    routes: ClassVar[tuple[RouteCall, ...]] = (
        RouteCall("PATCH", f"{ASSISTANT}/draft"),
        RouteCall("POST", f"{ASSISTANT}/publish"),
        RouteCall("GET", ASSISTANT),
        LIST_KBS,
    )
    owner_only: ClassVar[bool] = True
    risk: ClassVar[Any] = "medium"
    screen: ClassVar[str] = "/kbs"

    async def propose(self, ctx: ToolContext, args: Any) -> ProposalDraft | ToolResult:
        lang = ctx.language
        code = args.language if args.language in ("en", "hi", "hi-Latn") else None
        code = code or language_in(args.language, ctx.question)
        kb = await one_kb(ctx, args.kb)
        if isinstance(kb, ToolResult):
            return kb
        if code is None:
            return ask(ctx, "ask_language")
        try:
            state = await assistant_state(ctx, kb.get("id"))
        except ToolFailure:
            return ToolResult(markdown=ts("unavailable", lang), ok=False)
        route = f"/kbs/{kb.get('id')}?tab=assistant"
        if isinstance(state.get("draft"), dict):
            # Publishing would ship someone's other unpublished edits too.
            return ToolResult(
                markdown=ts("draft_pending", lang, kb=esc(kb.get("name"), 60)),
                link=self.link(ctx, route),
                ok=False,
            )
        name = ts(f"lang_{code}", lang)
        return ProposalDraft(
            title=ts("title_switch_reply_language", lang),
            summary=ts(
                "sum_switch_reply_language", lang, kb=esc(kb.get("name"), 60), language=name
            ),
            args=[(ts("a_kb", lang), str(kb.get("name"))), (ts("a_language", lang), name)],
            risk="medium",
            exec_args={
                "corpus_id": str(kb.get("id")),
                "kb_name": str(kb.get("name")),
                "language": code,
            },
        )

    async def execute(self, ctx: ToolContext, exec_args: dict[str, Any]) -> ActionOutcome:
        base = f"/api/v1/corpora/{exec_args['corpus_id']}/assistant"
        route = f"/kbs/{exec_args['corpus_id']}?tab=assistant"
        # Only when there is no draft: read again, because one may have appeared since the card.
        try:
            state = await assistant_state(ctx, exec_args["corpus_id"])
        except ToolFailure:
            state = {}
        if isinstance(state.get("draft"), dict):
            return ActionOutcome(
                method="PATCH",
                path=f"{ASSISTANT}/draft",
                status=409,
                ok=False,
                message=ts("draft_pending", ctx.language, kb=esc(exec_args["kb_name"], 60)),
                code="draft_pending",
                link=self.link(ctx, route),
            )
        patch = {"language": {"primary": exec_args["language"], "reply_policy": "fixed"}}
        saved = await ctx.client.request("PATCH", f"{base}/draft", json={"patch": patch})
        if not saved.ok:
            return self.done(ctx, saved, "PATCH", f"{ASSISTANT}/draft", "", link=route)
        body = saved.body if isinstance(saved.body, dict) else {}
        published = await ctx.client.request(
            "POST",
            f"{base}/publish",
            json={"base_revision": int(body.get("draft_revision") or 0)},
        )
        name = ts(f"lang_{exec_args['language']}", ctx.language)
        outcome = self.done(
            ctx,
            published,
            "POST",
            f"{ASSISTANT}/publish",
            ts(
                "done_switch_reply_language",
                ctx.language,
                kb=esc(exec_args["kb_name"], 60),
                language=name,
            ),
            link=route,
            result={"corpus_id": exec_args["corpus_id"], "language": exec_args["language"]},
        )
        if not outcome.ok:  # the draft was written but not published: say so
            outcome.message = f"{outcome.message} {ts('draft_left', ctx.language, kb=esc(exec_args['kb_name'], 60))}"
        return outcome


# -------------------------------------------------------------- start_guided_setup


class StartGuidedSetupArgs(StrictRequest):
    kb: str | None = None


class StartGuidedSetupExec(StrictRequest):
    corpus_id: uuid.UUID


class StartGuidedSetupTool(ActionBase):
    name: ClassVar[str] = "start_guided_setup"
    description: ClassVar[str] = (
        "Open the guided setup wizard of a knowledge base's assistant (args.kb). It changes "
        "nothing by itself."
    )
    args_model: ClassVar[type[StrictRequest]] = StartGuidedSetupArgs
    exec_model: ClassVar[type[StrictRequest]] = StartGuidedSetupExec
    roles: ClassVar[frozenset[CobRole]] = frozenset(
        {CobRole.OWNER, CobRole.MEMBER, CobRole.OPERATOR_OWNER}
    )
    routes: ClassVar[tuple[RouteCall, ...]] = (LIST_KBS,)
    risk: ClassVar[Any] = "low"
    screen: ClassVar[str] = "/kbs"

    async def propose(self, ctx: ToolContext, args: Any) -> ProposalDraft | ToolResult:
        kb = await one_kb(ctx, args.kb)
        if isinstance(kb, ToolResult):
            return kb
        return ToolResult(
            markdown=ts("setup_sentence", ctx.language, kb=esc(kb.get("name"), 60)),
            link=self.link(ctx, f"/kbs/{kb.get('id')}/setup"),
            data={"kb": str(kb.get("id"))},
        )

    async def execute(self, ctx: ToolContext, exec_args: dict[str, Any]) -> ActionOutcome:
        raise NotImplementedError("guided setup is a link, not a proposal")


register(ToggleAssistantTool())
register(PublishAssistantTool())
register(SwitchReplyLanguageTool())
register(StartGuidedSetupTool())
