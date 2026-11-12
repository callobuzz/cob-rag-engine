"""When was this updated? (TV5, decision D17.)

A cited chunk comes from one document version, and a version is created only when the content
changes (a website page is read again only if its text differs, BIZ2). So the version's creation
date is "when this information last changed". The assembler already loads the version with the
chunk, so no extra query is needed; this module only turns the moment into the business's local
date, never failing the answer.
"""

from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def updated_on(moment: datetime | None, tz: str) -> str | None:
    """`YYYY-MM-DD` of `moment` in the time zone `tz` (UTC when the zone is unknown), or None
    when there is no moment. A naive datetime is read as UTC; anything else is no date."""
    if not isinstance(moment, datetime):
        return None
    try:
        zone: tzinfo = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        zone = UTC
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return aware.astimezone(zone).date().isoformat()
