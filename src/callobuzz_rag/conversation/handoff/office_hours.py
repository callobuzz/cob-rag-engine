"""When does the business open next? (C5). Whether it is open NOW is C1's
`conversation.signals.office_state` (README pinned P7); this module only looks ahead, for the
closed-hours card ("We open again today at 2:00 pm").

Times are wall-clock times in the card's time zone. A day may hold several ranges; a range
whose close is not after its open runs past midnight and belongs to the day it opens; a
holiday has no opening that starts on it.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from callobuzz_rag.conversation.schema import BusinessCard
from callobuzz_rag.conversation.signals import office_state
from callobuzz_rag.conversation.types import OfficeState

_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_LOOKAHEAD_DAYS = 14

_WEEKDAYS: dict[str, tuple[str, ...]] = {
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    "hi": ("सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"),
    "hi-Latn": ("Somvar", "Mangalvar", "Budhvar", "Guruvar", "Shukravar", "Shanivar", "Ravivar"),
}
_TODAY = {"en": "today", "hi": "आज", "hi-Latn": "aaj"}
_TOMORROW = {"en": "tomorrow", "hi": "कल", "hi-Latn": "kal"}
_NOON_MIDNIGHT = {
    "en": ("midnight", "noon"),
    "hi": ("मध्यरात्रि", "दोपहर"),
    "hi-Latn": ("midnight", "noon"),
}
_AM_PM = {
    "en": ("am", "pm"),
    "hi": ("पूर्वाह्न", "अपराह्न"),
    "hi-Latn": ("am", "pm"),
}


def _hm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def _starts(card: BusinessCard, day: date, tz: ZoneInfo) -> list[datetime]:
    """Opening moments that start on `day` (local), as aware datetimes."""
    if day in {h.date for h in card.holidays}:
        return []
    name = _DAYS[day.weekday()]
    return sorted(datetime.combine(day, _hm(r.open), tz) for r in card.hours if r.day == name)


def next_open(card: BusinessCard, now: datetime) -> datetime | None:
    """The next opening moment within 14 days (aware, in the card's zone), or None."""
    if now.tzinfo is None:
        raise ValueError("next_open needs a timezone-aware datetime")
    if not card.hours:
        return None
    tz = ZoneInfo(card.timezone)
    local = now.astimezone(tz)
    for offset in range(_LOOKAHEAD_DAYS + 1):
        later = [s for s in _starts(card, local.date() + timedelta(days=offset), tz) if s > local]
        if later:
            return later[0]
    return None


def _clock(moment: datetime, lang: str) -> str:
    if moment.minute == 0 and moment.hour in (0, 12):
        return _NOON_MIDNIGHT.get(lang, _NOON_MIDNIGHT["en"])[moment.hour == 12]
    am, pm = _AM_PM.get(lang, _AM_PM["en"])
    hour = moment.hour % 12 or 12
    return f"{hour}:{moment.minute:02d} {pm if moment.hour >= 12 else am}"


def _midnight_line(opening: datetime, days: int, key: str) -> str:
    """An opening at 00:00 is "tonight" when it is the next day, else the start of a named day:
    "tomorrow at midnight" would read as the end of tomorrow."""
    weekday = _WEEKDAYS[key][opening.weekday()]
    if key == "en":
        if days == 1:
            return "We open again at midnight tonight."
        return f"We open again at 12 midnight as {weekday} begins."
    if key == "hi":
        if days == 1:
            return "हम आज रात 12 बजे फिर खुलेंगे।"
        return f"हम {weekday} शुरू होते ही, रात 12 बजे, फिर खुलेंगे।"
    if days == 1:
        return "Hum aaj raat 12 baje phir khulenge."
    return f"Hum {weekday} shuru hote hi, raat 12 baje, phir khulenge."


def opens_again_line(card: BusinessCard, now: datetime, lang: str) -> str | None:
    """ "We open again today at 2:00 pm." / "...on Wednesday at 9:00 am." in the visitor's
    language, or None when the card has no hours (or no opening in the next two weeks)."""
    if office_state(card, now) is OfficeState.OPEN:
        return None  # the card says open now: "opens again" would be a false statement
    opening = next_open(card, now)
    if opening is None:
        return None
    key = lang if lang in _WEEKDAYS else "en"
    local = now.astimezone(ZoneInfo(card.timezone)).date()
    days = (opening.date() - local).days
    when = _clock(opening, key)
    if opening.hour == 0 and opening.minute == 0 and days >= 1:
        return _midnight_line(opening, days, key)
    if key == "en":
        if days == 0:
            return f"We open again today at {when}."
        if days == 1:
            return f"We open again tomorrow at {when}."
        return f"We open again on {_WEEKDAYS['en'][opening.weekday()]} at {when}."
    day = (
        _TODAY[key]
        if days == 0
        else _TOMORROW[key]
        if days == 1
        else _WEEKDAYS[key][opening.weekday()]
    )
    if key == "hi":
        return f"हम {day} {when} पर फिर खुलेंगे।"
    return f"Hum {day} {when} par phir khulenge."
