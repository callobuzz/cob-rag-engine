# src/callobuzz_rag/conversation/offers.py
"""The seam later epics plug into: lead form (C4), handoff card (C5), CTA and quick
replies (C6), plus pre-turn hooks (C4's conversational lead capture). Providers register at
import time; the director asks after the answer text is final. At most one `ui` block:
handoff card > lead form (a provider returning a `str` — a Markdown paragraph appended to
the answer — takes that slot too); a CTA is suppressed when a `ui` block is shown; quick
replies always allowed. Forced mode: the asked provider builds even when its own
`wanted()` says no (README P9)."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.conversation.schema import AssistantConfig, QuickReply
    from callobuzz_rag.conversation.signals import TurnSignals
    from callobuzz_rag.conversation.state import ConversationState
    from callobuzz_rag.conversation.types import Channel
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.llm.base import LLMProvider

logger = logging.getLogger(__name__)


class OfferKind(StrEnum):
    LEAD_FORM = "lead_form"
    HANDOFF_CARD = "handoff_card"
    CTA = "cta"
    QUICK_REPLIES = "quick_replies"


@dataclass(frozen=True)
class OfferContext:
    config: "AssistantConfig"
    signals: "TurnSignals"
    state: "ConversationState"
    language: str
    corpus_id: UUID
    conversation_id: UUID | None
    channel: "Channel"
    answer_text: str
    cited_sources: list[dict[str, Any]]
    llm: "LLMProvider | None" = None  # the corpus's provider (fast_call_options for C6 follow-ups)
    probe: Callable[[str], Awaitable[bool]] | None = None  # retrieve-only gate check (C6)
    question: str = ""  # the masked visitor message
    ui_shown: bool = False  # set by collect() for the second pass
    ui_missing_for_fallback: bool = False  # no_answer turn whose ui pass built nothing
    ref_code: str | None = None  # TV2 reference: the conversation's reference code, if any


class OfferProvider(Protocol):
    kind: OfferKind

    def wanted(self, ctx: OfferContext) -> str | None: ...

    async def build(self, ctx: OfferContext, reason: str) -> Any: ...


@dataclass(frozen=True)
class PreTurnContext:
    """What a pre-turn hook sees, right after PII masking and before labelling. Hooks open
    their own sessions from `session_factory` and commit what they write."""

    config: "AssistantConfig"
    state: "ConversationState"
    text: str  # masked
    language: str
    corpus_id: UUID
    tenant_id: UUID
    conversation_id: UUID | None
    widget_key_id: UUID | None
    channel: "Channel"
    page_url: str | None
    utm: dict[str, str] | None
    session_factory: "Callable[[], AsyncSession]"
    settings: "Settings"
    now: datetime
    is_preview: bool
    contact_phone: str | None = None  # TV6 channel: the customer's own number, if the channel knows


@dataclass(frozen=True)
class PreTurnResult:
    """`stored_text` (when not None) replaces the visitor message for everything after the
    hook — labelling, the transcript (e.g. "[phone shared]"); a non-None `reply` ends the
    turn with route `lead` and this text as the answer."""

    stored_text: str | None
    reply: str | None
    quick_replies: list["QuickReply"] = field(default_factory=list)


PreTurnHook = Callable[[PreTurnContext], Awaitable[PreTurnResult | None]]


@dataclass
class CollectedOffers:
    ui: Any | None = None
    cta: Any | None = None
    quick_replies: list[Any] = field(default_factory=list)
    paragraphs: list[str] = field(default_factory=list)
    reasons: list[tuple[str, str]] = field(default_factory=list)


_UI_ORDER = (OfferKind.HANDOFF_CARD, OfferKind.LEAD_FORM)


class OfferRegistry:
    def __init__(self) -> None:
        self._providers: list[OfferProvider] = []
        self._pre_turn: list[PreTurnHook] = []

    def register(self, provider: OfferProvider) -> None:
        self._providers.append(provider)

    def register_pre_turn(self, hook: PreTurnHook) -> None:
        self._pre_turn.append(hook)

    def pre_turn_hooks(self) -> list[PreTurnHook]:
        return list(self._pre_turn)

    def providers(self, kind: OfferKind) -> list[OfferProvider]:
        return [p for p in self._providers if p.kind == kind]

    def has(self, kind: OfferKind) -> bool:
        return bool(self.providers(kind))

    async def _first(
        self, ctx: OfferContext, kind: OfferKind, *, forced: bool
    ) -> tuple[Any, str] | None:
        for provider in self.providers(kind):
            reason = provider.wanted(ctx) or ("forced" if forced else None)
            if reason is None:
                continue
            try:
                built = await provider.build(ctx, reason)
            except Exception:
                logger.warning("Offer provider %s failed", kind.value, exc_info=True)
                continue
            if built is not None:
                return built, reason
        return None

    @staticmethod
    def _place_ui(out: CollectedOffers, built: Any) -> None:
        if isinstance(built, str):
            out.paragraphs.append(built)
        else:
            out.ui = built

    async def collect(
        self, ctx: OfferContext, *, ui_allowed: bool, forced: OfferKind | None = None
    ) -> CollectedOffers:
        out = CollectedOffers()
        if forced is not None:
            hit = await self._first(ctx, forced, forced=True)
            if hit is not None:
                out.reasons.append((forced.value, hit[1]))
                if forced in _UI_ORDER:
                    self._place_ui(out, hit[0])
                elif forced == OfferKind.CTA:
                    out.cta = hit[0]
            return out
        # Pass 1: the ui block (handoff card > lead form).
        ui_taken = False
        if ui_allowed:
            for kind in _UI_ORDER:
                hit = await self._first(ctx, kind, forced=False)
                if hit is not None:
                    self._place_ui(out, hit[0])
                    out.reasons.append((kind.value, hit[1]))
                    ui_taken = True
                    break
        # Pass 2: CTA and quick replies see what pass 1 did.
        second = replace(
            ctx,
            ui_shown=ui_taken,
            ui_missing_for_fallback=bool(ctx.signals.no_answer and not ui_taken),
        )
        if not ui_taken:
            hit = await self._first(second, OfferKind.CTA, forced=False)
            if hit is not None:
                if isinstance(hit[0], str):
                    out.paragraphs.append(hit[0])
                else:
                    out.cta = hit[0]
                out.reasons.append((OfferKind.CTA.value, hit[1]))
        hit = await self._first(second, OfferKind.QUICK_REPLIES, forced=False)
        if hit is not None:
            out.quick_replies = list(hit[0])[:4]
            out.reasons.append((OfferKind.QUICK_REPLIES.value, hit[1]))
        return out


registry = OfferRegistry()
