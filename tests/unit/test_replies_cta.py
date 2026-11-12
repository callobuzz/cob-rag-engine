"""The CTA builder and the CTA policy (conversation C6)."""

from callobuzz_rag.api.schemas.assistant import Cta
from callobuzz_rag.conversation.replies import build_cta, choose_cta
from callobuzz_rag.conversation.schema import HandoffChannel
from callobuzz_rag.conversation.state import ConversationState
from callobuzz_rag.conversation.types import OfficeState, Route, TurnLabel
from tests.unit.replies_helpers import replies_config as _config
from tests.unit.replies_helpers import sales_config

HI_WA = "WhatsApp पर बात करें"


def _kw(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "state": ConversationState(),
        "ui_shown": False,
        "language": "en",
        "prefill": "q",
        "fallback_ui_missing": False,
    }
    base.update(over)
    return base


def test_build_cta_per_type_and_language() -> None:
    cfg = sales_config()
    wa = build_cta(cfg, "whatsapp", language="hi", prefill="CBC kitne ka hai?")
    assert wa is not None and wa.type == "whatsapp" and wa.label == HI_WA
    assert wa.url is not None and wa.url.startswith("https://wa.me/919812345678?text=")
    assert "CBC%20kitne" in wa.url
    call = build_cta(cfg, "call", language="en", prefill="")
    assert call is not None and call.url == "tel:+919812345678" and call.label == "Call us"
    book = build_cta(cfg, "booking", language="hi-Latn", prefill="")
    assert book is not None and book.url == "https://shree-labs.in/book"
    assert book.label == "Slot book karein"
    assert build_cta(cfg, "handoff", language="en", prefill="") == Cta(
        type="handoff", label="Talk to a person", url=None
    )
    assert build_cta(cfg, "lead_form", language="en", prefill="") is None  # C4's form
    assert build_cta(cfg, "none", language="en", prefill="") is None


def test_whatsapp_prefill_is_encoded_and_capped() -> None:
    cfg = sales_config()
    cta = build_cta(cfg, "whatsapp", language="en", prefill="Price of CBC & ESR?")
    assert cta is not None and cta.url is not None
    assert cta.url.endswith("Hi%21%20Price%20of%20CBC%20%26%20ESR%3F")
    long = build_cta(cfg, "whatsapp", language="en", prefill="word " * 400)
    assert long is not None and long.url is not None and len(long.url) < 1200


def test_build_cta_skips_missing_targets() -> None:
    cfg = _config()  # empty business card
    assert build_cta(cfg, "whatsapp", language="en", prefill="x") is None
    assert build_cta(cfg, "call", language="en", prefill="x") is None
    assert build_cta(cfg, "booking", language="en", prefill="x") is None
    assert build_cta(cfg, "link", language="en", prefill="x") is None


def test_link_cta_uses_owner_label() -> None:
    cfg = _config()
    cfg.conversation.cta.link_url = "https://shree-labs.in/packages"
    cfg.conversation.cta.link_label = {"en": "See health packages"}
    cta = build_cta(cfg, "link", language="hi", prefill="")
    assert cta == Cta(
        type="link", label="See health packages", url="https://shree-labs.in/packages"
    )


def test_channel_override_and_enabled_flag_are_honoured() -> None:
    cfg = sales_config()
    wa = next(c for c in cfg.handoff.channels if c.kind == "whatsapp")
    wa.value = "+911111111111"
    cta = build_cta(cfg, "whatsapp", language="en", prefill="")
    assert cta is not None and cta.url is not None and "wa.me/911111111111" in cta.url
    wa.enabled = False
    assert build_cta(cfg, "whatsapp", language="en", prefill="") is None


def test_call_cta_hidden_when_the_call_channel_is_closed_now() -> None:
    cfg = sales_config()  # the default call channel is open_hours only
    assert build_cta(cfg, "call", language="en", prefill="", office=OfficeState.CLOSED) is None
    assert build_cta(cfg, "call", language="en", prefill="", office=OfficeState.OPEN) is not None
    assert build_cta(cfg, "call", language="en", prefill="", office=OfficeState.UNKNOWN)


def test_handoff_cta_needs_handoff_enabled_and_a_channel() -> None:
    cfg = sales_config("handoff")
    assert build_cta(cfg, "handoff", language="en", prefill="") is not None
    cfg.handoff.enabled = False
    assert build_cta(cfg, "handoff", language="en", prefill="") is None
    off = _config()
    off.handoff.channels = []
    assert build_cta(off, "handoff", language="en", prefill="") is None


def test_choose_cta_only_on_buying_intent_answers() -> None:
    cfg = sales_config()
    got = choose_cta(cfg, label=TurnLabel.BUYING_INTENT, route=Route.GROUNDED, **_kw())
    assert got is not None
    other = choose_cta(cfg, label=TurnLabel.BUSINESS_QUESTION, route=Route.GROUNDED, **_kw())
    assert other is None
    declined = choose_cta(cfg, label=TurnLabel.BUYING_INTENT, route=Route.DECLINE, **_kw())
    assert declined is None


def test_choose_cta_never_repeats_a_declined_type_and_yields_to_ui() -> None:
    cfg, state = sales_config(), ConversationState()
    state.decline_cta("whatsapp")
    gone = choose_cta(cfg, label=TurnLabel.BUYING_INTENT, route=Route.GROUNDED, **_kw(state=state))
    assert gone is None
    yielded = choose_cta(
        cfg, label=TurnLabel.BUYING_INTENT, route=Route.GROUNDED, **_kw(ui_shown=True)
    )
    assert yielded is None


def test_choose_cta_zero_per_answer_disables() -> None:
    cfg = sales_config()
    cfg.conversation.cta.max_per_answer = 0
    assert choose_cta(cfg, label=TurnLabel.BUYING_INTENT, route=Route.GROUNDED, **_kw()) is None


def test_lead_form_choice_is_not_a_cta() -> None:
    cfg = sales_config("lead_form")
    assert choose_cta(cfg, label=TurnLabel.BUYING_INTENT, route=Route.GROUNDED, **_kw()) is None


def test_no_cta_when_the_topics_lead_form_was_dismissed() -> None:
    cfg = sales_config()
    cfg.leads.triggers.topics = ["tests"]
    state = ConversationState(lead_dismissed=True)
    kw = _kw(state=state, topic_id="tests")
    assert choose_cta(cfg, label=TurnLabel.BUYING_INTENT, route=Route.GROUNDED, **kw) is None
    other = _kw(state=state, topic_id="home")
    assert choose_cta(cfg, label=TurnLabel.BUYING_INTENT, route=Route.GROUNDED, **other)


def test_no_answer_falls_back_to_whatsapp_then_call_when_no_ui_fired() -> None:
    cfg = sales_config()
    state = ConversationState()
    kw = _kw(state=state, fallback_ui_missing=True)
    cta = choose_cta(cfg, label=TurnLabel.BUSINESS_QUESTION, route=Route.NO_ANSWER, **kw)
    assert cta is not None and cta.type == "whatsapp"
    state.decline_cta("whatsapp")
    cta = choose_cta(cfg, label=TurnLabel.BUSINESS_QUESTION, route=Route.NO_ANSWER, **kw)
    assert cta is not None and cta.type == "call"
    cfg.scope.fallback.actions = []
    quiet = _kw(fallback_ui_missing=True)
    assert (
        choose_cta(cfg, label=TurnLabel.BUSINESS_QUESTION, route=Route.NO_ANSWER, **quiet) is None
    )


def test_state_click_counters() -> None:
    state = ConversationState()
    state.record_cta_shown("call")
    state.record_cta_shown("call")
    state.record_cta_click("call")
    state.record_chip_click()
    assert state.cta_shown == ["call"] and state.cta_clicks == 1 and state.chip_clicks == 1
    state.decline_cta("call")
    state.decline_cta("call")
    assert state.cta_declined == ["call"]


def test_event_owned_keys_merge_instead_of_being_overwritten() -> None:
    mine = ConversationState(cta_declined=["call"], chip_clicks=1, cta_clicks=5)
    stored = {"cta_declined": ["whatsapp"], "chip_clicks": 3, "cta_clicks": 2}
    mine.merge_event_keys(stored)
    assert mine.cta_declined == ["call", "whatsapp"]
    assert mine.chip_clicks == 3 and mine.cta_clicks == 5
    mine.merge_event_keys(None)
    assert mine.chip_clicks == 3


def test_the_handoff_button_follows_the_cards_channels() -> None:
    cfg = _config()  # empty business card
    cfg.handoff.channels = [HandoffChannel(kind="email", value="not-an-address")]
    assert build_cta(cfg, "handoff", language="en", prefill="") is None  # the card shows nothing
    cfg.handoff.channels = [HandoffChannel(kind="email", value="help@shree-labs.in")]
    assert build_cta(cfg, "handoff", language="en", prefill="") is not None


def test_a_listed_channel_with_no_usable_value_does_not_hide_a_later_one() -> None:
    cfg = sales_config()
    cfg.handoff.channels = [
        HandoffChannel(kind="whatsapp", value="junk"),
        HandoffChannel(kind="whatsapp", value="+919800000001"),
    ]
    cta = build_cta(cfg, "whatsapp", language="en", prefill="hi")
    assert cta is not None and cta.url is not None and "919800000001" in cta.url
