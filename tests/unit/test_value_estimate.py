"""The value formula: pure, integer-only, always with its formula lines (TV4)."""

import random
from datetime import UTC, date, datetime
from typing import Any

import pytest

from callobuzz_rag.business.value import (
    ValueInputs,
    ValueSettingsIn,
    estimate,
    month_bounds,
    month_of,
    previous_month,
)
from callobuzz_rag.business.value_report import (
    format_duration,
    format_value_money,
)

S = ValueSettingsIn(
    lead_value_minor=150_000,
    handoff_value_minor=80_000,
    staff_hour_minor=25_000,
    minutes_per_answer=3,
)


def _inputs(**kw: Any) -> ValueInputs:
    base: dict[str, Any] = dict(
        conversations=0,
        questions=0,
        answered=0,
        answered_after_hours=0,
        leads_total=0,
        leads_qualified=0,
        leads_partial=0,
        handoffs=0,
        bookings=0,
        median_first_reply_ms=None,
        top_questions=[],
        unanswered=[],
    )
    return ValueInputs(**{**base, **kw})


def test_formula_shree_dental_month() -> None:
    b = estimate(_inputs(answered=412, leads_qualified=9, handoffs=23), S)
    assert b.leads_minor == 9 * 150_000  # Rs 13,500
    assert b.handoffs_minor == 23 * 80_000  # Rs 18,400
    assert b.minutes_saved == 1_236
    assert b.time_minor == 1_236 * 25_000 // 60  # Rs 5,150 (floor of paise)
    assert b.total_minor == b.leads_minor + b.handoffs_minor + b.time_minor


def test_partial_leads_and_bookings_do_not_add_money() -> None:
    a = estimate(_inputs(leads_qualified=1), S).total_minor
    assert estimate(_inputs(leads_qualified=1, leads_partial=7, bookings=4), S).total_minor == a


def test_zero_settings_give_zero_value_but_keep_the_counts() -> None:
    b = estimate(_inputs(answered=50, handoffs=3), ValueSettingsIn(0, 0, 0, 3))
    assert b.total_minor == 0 and b.minutes_saved == 150


@pytest.mark.parametrize("seed", range(300))
def test_integers_only_and_monotonic(seed: int) -> None:
    rng = random.Random(seed)
    answered, leads, handoffs = (rng.randint(0, 10**6) for _ in range(3))
    money, minutes = rng.randint(0, 10**9), rng.randint(0, 120)
    s = ValueSettingsIn(money, money, money, minutes)
    b = estimate(_inputs(answered=answered, leads_qualified=leads, handoffs=handoffs), s)
    assert all(
        isinstance(v, int) for v in (b.leads_minor, b.handoffs_minor, b.time_minor, b.total_minor)
    )
    more = estimate(_inputs(answered=answered + 1, leads_qualified=leads, handoffs=handoffs), s)
    assert more.total_minor >= b.total_minor


def test_formula_lines_name_the_settings() -> None:
    lines = estimate(_inputs(answered=10, leads_qualified=2, handoffs=1), S).formula
    assert [ln.key for ln in lines] == ["leads", "handoffs", "time"]
    assert lines[2].inputs == {"minutes_saved": 30, "staff_hour_minor": 25_000}
    assert lines[0].inputs == {"count": 2, "value_minor": 150_000}
    assert (
        sum(ln.amount_minor for ln in lines)
        == estimate(_inputs(answered=10, leads_qualified=2, handoffs=1), S).total_minor
    )


def test_no_floats_in_the_money_path() -> None:
    import ast
    from pathlib import Path

    import callobuzz_rag.business.value as value

    tree = ast.parse(Path(value.__file__).read_text(encoding="utf-8"))
    calls = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "float" not in calls
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Div)]


# ------------------------------------------------------------------ words, months


def test_money_is_shown_in_whole_units_with_indian_grouping() -> None:
    assert format_value_money(1_350_000, "INR") == "₹13,500"
    assert format_value_money(15_000_000, "INR") == "₹1,50,000"
    assert format_value_money(515_083, "INR") == "₹5,150.83"
    assert format_value_money(1_500, "USD") == "$15"


@pytest.mark.parametrize(
    ("minutes", "text"), [(0, "0 min"), (45, "45 min"), (120, "2 h"), (1_236, "20 h 36 min")]
)
def test_durations(minutes: int, text: str) -> None:
    assert format_duration(minutes) == text


def test_a_month_follows_the_business_time_zone() -> None:
    # 2026-09-30 19:00 UTC is 00:30 IST on 1 October
    start, end = month_bounds(date(2026, 10, 1), "Asia/Kolkata")
    assert start == datetime(2026, 9, 30, 18, 30, tzinfo=UTC)
    assert end == datetime(2026, 10, 31, 18, 30, tzinfo=UTC)
    assert start <= datetime(2026, 9, 30, 19, 0, tzinfo=UTC) < end
    assert month_of(datetime(2026, 9, 30, 19, 0, tzinfo=UTC), "Asia/Kolkata") == date(2026, 10, 1)
    assert month_of(datetime(2026, 9, 30, 19, 0, tzinfo=UTC), "UTC") == date(2026, 9, 1)


def test_month_bounds_handle_year_end_and_bad_zones() -> None:
    _, end = month_bounds(date(2026, 12, 15), "Asia/Kolkata")
    assert end == datetime(2026, 12, 31, 18, 30, tzinfo=UTC)
    assert month_bounds(date(2026, 10, 1), "Nowhere/Land") == month_bounds(
        date(2026, 10, 1), "Asia/Kolkata"
    )
    assert previous_month(date(2026, 1, 1)) == date(2025, 12, 1)


# ------------------------------------------------------------------ review fixes (TV4)


def test_every_channel_is_visitor_or_not() -> None:
    """A new channel must be decided on here: the report counts only what visitors wrote."""
    from callobuzz_rag.business.value import NON_VISITOR_CHANNELS, VISITOR_CHANNELS
    from callobuzz_rag.conversation.types import Channel

    assert VISITOR_CHANNELS.isdisjoint(NON_VISITOR_CHANNELS)
    assert {c.value for c in Channel} == VISITOR_CHANNELS | NON_VISITOR_CHANNELS
    assert {"widget", "whatsapp"} <= VISITOR_CHANNELS
    # the dashboard Playground, the onboarding Try step and admin tests use these
    assert {"stream", "api", "playground", "cob"} <= NON_VISITOR_CHANNELS


def test_the_turn_sql_filters_to_visitor_channels_only() -> None:
    from callobuzz_rag.business import value

    assert "channel IN ('conversation', 'whatsapp', 'widget')" in value._TURNS
    assert "'stream'" not in value._TURNS and "<> 'cob'" not in value._TURNS


def test_conversations_are_counted_by_conversation_id_only() -> None:
    """A turn without a conversation is not a conversation of its own."""
    from callobuzz_rag.business import value

    assert "count(DISTINCT conversation_id)" in value._COUNTS_SQL
    assert "COALESCE(conversation_id" not in value._COUNTS_SQL


def test_leads_without_qualification_rules_count_and_are_labelled_leads() -> None:
    from callobuzz_rag.business import value

    sql = " ".join(value._LEADS_SQL.split())
    assert "qualification IS NULL" in sql  # no rules: not rated, still a lead
    i = _inputs(leads_qualified=40, leads_unrated=40)
    assert estimate(i, S).leads_minor == 40 * 150_000
    from callobuzz_rag.business.value_report import formula_lines

    lines = formula_lines(i, S, estimate(i, S), "INR")
    assert lines[0].text.startswith("40 leads ")
    rated = _inputs(leads_qualified=9)
    text = formula_lines(rated, S, estimate(rated, S), "INR")[0].text
    assert text.startswith("9 qualified leads ")


def test_a_callback_pair_is_valued_once() -> None:
    from callobuzz_rag.business import value

    sql = " ".join(value._HANDOFFS_SQL.split())
    # a callback handoff whose lead already counts is not a second request
    assert "lead_id" in sql and "leads" in sql
