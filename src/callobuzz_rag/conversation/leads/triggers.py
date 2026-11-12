"""When to offer the lead form (research recommendation F).

At most once per conversation, never after a lead exists, never instead of the answer.
A visitor who asks to be contacted is always offered it. Only the website widget can
submit a lead, so no other surface (`/query`, conversations, the playground) is offered
the form, and a preview turn never creates a real lead. WhatsApp (TV6) is offered a one-line
consent question instead of the form.
"""

from typing import Any

from callobuzz_rag.conversation.page_glob import matches
from callobuzz_rag.conversation.types import Channel, OfficeState, Route, TurnLabel


def _explicit_cta_wins(ctx: Any) -> bool:
    """True when the owner chose a call to action for buying intent (WhatsApp, call,
    booking, link, handoff) and it can be shown on this turn: then it, not the form, takes
    the slot. The form keeps the slot under the default policy ("lead_form"), under "none",
    when that CTA has no target, and when the visitor already dismissed that CTA type."""
    # Deferred: replies imports the offer registry, which imports this package's siblings.
    from callobuzz_rag.conversation.replies import choose_cta

    signals = ctx.signals
    return (
        choose_cta(
            ctx.config,
            label=signals.label,
            route=signals.route,
            state=ctx.state,
            ui_shown=False,
            language=ctx.language,
            prefill=getattr(ctx, "question", ""),
            fallback_ui_missing=False,
            office=signals.office,
            topic_id=signals.topic_id,
        )
        is not None
    )


def lead_trigger(ctx: Any) -> str | None:
    leads = ctx.config.leads
    if not leads.enabled or ctx.state.lead_id:
        return None
    signals, triggers = ctx.signals, leads.triggers
    if ctx.channel not in (Channel.WIDGET, Channel.WHATSAPP):  # TV6 channel
        return None
    if getattr(signals, "is_preview", False):
        return None
    if getattr(signals, "asked_for_contact", False):
        return "asked"
    if ctx.signals.label == TurnLabel.BUYING_INTENT and _explicit_cta_wins(ctx):
        return None
    if ctx.state.lead_offers > 0 or ctx.state.lead_dismissed:
        return None
    if triggers.buying_intent and signals.label == TurnLabel.BUYING_INTENT:
        return "buying_intent"
    if triggers.no_answer and signals.route == Route.NO_ANSWER:
        return "no_answer"
    if triggers.topics and signals.topic_id in triggers.topics:
        return "topic"
    if signals.page_url and any(matches(g, signals.page_url) for g in triggers.page_url_globs):
        return "page_url"
    if triggers.human_request and signals.label == TurnLabel.HUMAN_REQUEST:
        return "human_request"
    if triggers.out_of_hours and signals.office == OfficeState.CLOSED:
        return "out_of_hours"
    if triggers.after_n_messages and ctx.state.message_count >= triggers.after_n_messages:
        return "after_n_messages"
    return None
