"""Office hours from the business card: C1's office_state (pinned P7) and C5's next_open."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from callobuzz_rag.conversation.handoff.office_hours import next_open, opens_again_line
from callobuzz_rag.conversation.schema import BusinessCard
from callobuzz_rag.conversation.signals import office_state
from callobuzz_rag.conversation.types import OfficeState


def _card(**kw: object) -> BusinessCard:
    weekdays = ("mon", "tue", "wed", "thu", "fri")
    base: dict[str, object] = {
        "timezone": "Asia/Kolkata",
        "hours": [{"day": d, "open": "09:00", "close": "13:00"} for d in weekdays]
        + [{"day": d, "open": "14:00", "close": "18:30"} for d in weekdays]
        + [{"day": "sat", "open": "22:00", "close": "02:00"}],
        "holidays": [{"date": "2026-10-20", "name": "Diwali"}],
    }
    base.update(kw)
    return BusinessCard.model_validate(base)


def ist(y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=ZoneInfo("Asia/Kolkata")).astimezone(UTC)


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (ist(2026, 10, 5, 10), OfficeState.OPEN),  # Monday morning
        (ist(2026, 10, 5, 13, 30), OfficeState.CLOSED),  # lunch break
        (ist(2026, 10, 5, 18, 30), OfficeState.CLOSED),  # close is exclusive
        (ist(2026, 10, 5, 8, 59), OfficeState.CLOSED),
        (ist(2026, 10, 10, 23), OfficeState.OPEN),  # Saturday night range
        (ist(2026, 10, 11, 1, 30), OfficeState.OPEN),  # its overnight tail on Sunday
        (ist(2026, 10, 11, 2), OfficeState.CLOSED),
        (ist(2026, 10, 11, 12), OfficeState.CLOSED),  # Sunday: no hours
        (ist(2026, 10, 20, 10), OfficeState.CLOSED),  # Tuesday, but Diwali
    ],
)
def test_office_state(moment: datetime, expected: OfficeState) -> None:
    assert office_state(_card(), moment) is expected


def test_a_holiday_closes_the_overnight_tail_too() -> None:
    card = _card(holidays=[{"date": "2026-10-11", "name": "Holiday"}])
    assert office_state(card, ist(2026, 10, 11, 1, 30)) is OfficeState.CLOSED


def test_no_hours_is_unknown() -> None:
    assert office_state(_card(hours=[]), ist(2026, 10, 5, 10)) is OfficeState.UNKNOWN


def test_timezone_is_the_cards() -> None:
    card = _card(timezone="Asia/Dubai", hours=[{"day": "mon", "open": "09:00", "close": "10:00"}])
    assert office_state(card, datetime(2026, 10, 5, 5, 30, tzinfo=UTC)) is OfficeState.OPEN
    assert office_state(card, ist(2026, 10, 5, 9, 30)) is OfficeState.CLOSED


def test_next_open_refuses_a_naive_datetime() -> None:
    with pytest.raises(ValueError):
        next_open(_card(), datetime(2026, 10, 5, 10))


def test_next_open_after_lunch_and_over_the_holiday() -> None:
    assert next_open(_card(), ist(2026, 10, 5, 13, 30)) == ist(2026, 10, 5, 14)
    assert next_open(_card(), ist(2026, 10, 19, 19)) == ist(2026, 10, 21, 9)
    assert next_open(_card(hours=[]), ist(2026, 10, 5, 10)) is None


def test_next_open_agrees_with_office_state() -> None:
    moment = ist(2026, 10, 10, 20)
    assert office_state(_card(), moment) is OfficeState.CLOSED
    opening = next_open(_card(), moment)
    assert opening == ist(2026, 10, 10, 22)
    assert opening is not None and office_state(_card(), opening) is OfficeState.OPEN


def test_holiday_dates_are_dates() -> None:
    assert _card().holidays[0].date == date(2026, 10, 20)


def test_opens_again_line_in_three_languages() -> None:
    lunch = ist(2026, 10, 5, 13, 30)
    assert opens_again_line(_card(), lunch, "en") == "We open again today at 2:00 pm."
    assert opens_again_line(_card(), lunch, "hi-Latn") == "Hum aaj 2:00 pm par phir khulenge."
    assert opens_again_line(_card(), lunch, "hi") == "हम आज 2:00 अपराह्न पर फिर खुलेंगे।"
    evening = ist(2026, 10, 5, 19)
    assert opens_again_line(_card(), evening, "en") == "We open again tomorrow at 9:00 am."
    over_holiday = ist(2026, 10, 19, 19)
    assert opens_again_line(_card(), over_holiday, "en") == "We open again on Wednesday at 9:00 am."
    assert opens_again_line(_card(hours=[]), lunch, "en") is None
    assert opens_again_line(_card(), lunch, "ta") == "We open again today at 2:00 pm."


def _all_day(**kw: object) -> BusinessCard:
    days = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    return _card(hours=[{"day": d, "open": "00:00", "close": "23:59"} for d in days], **kw)


def test_a_midnight_opening_reads_as_midnight_not_12_am() -> None:
    card = _card(hours=[{"day": "tue", "open": "00:00", "close": "06:00"}])
    line = opens_again_line(card, ist(2026, 10, 5, 20), "en")
    assert line == "We open again at midnight tonight."
    assert "12:00 am" not in line


def test_noon_reads_as_noon() -> None:
    card = _card(hours=[{"day": "mon", "open": "12:00", "close": "18:00"}])
    assert opens_again_line(card, ist(2026, 10, 5, 8), "en") == "We open again today at noon."


def test_an_all_day_card_is_open_up_to_the_last_minute() -> None:
    assert office_state(_all_day(), ist(2026, 10, 5, 23, 59)) is OfficeState.OPEN


def test_an_all_day_card_with_a_holiday_opens_after_it_at_midnight() -> None:
    card = _all_day()
    moment = ist(2026, 10, 20, 10)  # Diwali (a Tuesday)
    assert office_state(card, moment) is OfficeState.CLOSED
    assert opens_again_line(card, moment, "en") == "We open again at midnight tonight."


def test_no_opening_line_when_the_card_says_it_is_open_now() -> None:
    # A closed state that came from somewhere else (a forced preview) must not claim the
    # business "opens again" at some later time.
    assert opens_again_line(_all_day(), ist(2026, 10, 5, 10), "en") is None


def test_overnight_range_next_open_is_its_start() -> None:
    card = _card(hours=[{"day": "fri", "open": "18:00", "close": "02:00"}])
    sat_noon = ist(2026, 10, 10, 12)
    assert office_state(card, sat_noon) is OfficeState.CLOSED
    assert next_open(card, sat_noon) == ist(2026, 10, 16, 18)
    assert opens_again_line(card, sat_noon, "en") == "We open again on Friday at 6:00 pm."


def test_next_open_is_in_the_cards_timezone_across_utc_midnight() -> None:
    # 20:00 UTC on Oct 5 is 01:30 IST on Oct 6: the card's Tuesday, not Monday.
    card = _card(hours=[{"day": "tue", "open": "09:00", "close": "17:00"}])
    late = datetime(2026, 10, 5, 20, 0, tzinfo=UTC)
    assert next_open(card, late) == ist(2026, 10, 6, 9)
    assert opens_again_line(card, late, "en") == "We open again today at 9:00 am."


def test_a_midnight_opening_days_away_names_the_weekday_that_begins() -> None:
    card = _card(hours=[{"day": "wed", "open": "00:00", "close": "06:00"}])
    now = ist(2026, 10, 5, 20)  # Monday evening, opening Wednesday 00:00
    assert opens_again_line(card, now, "en") == "We open again at 12 midnight as Wednesday begins."
    assert opens_again_line(card, now, "hi-Latn") == (
        "Hum Budhvar shuru hote hi, raat 12 baje, phir khulenge."
    )
    assert opens_again_line(card, now, "hi") == "हम बुधवार शुरू होते ही, रात 12 बजे, फिर खुलेंगे।"


def test_a_midnight_opening_tonight_in_hindi() -> None:
    card = _card(hours=[{"day": "tue", "open": "00:00", "close": "06:00"}])
    now = ist(2026, 10, 5, 20)
    assert opens_again_line(card, now, "hi") == "हम आज रात 12 बजे फिर खुलेंगे।"
    assert opens_again_line(card, now, "hi-Latn") == "Hum aaj raat 12 baje phir khulenge."
