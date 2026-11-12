"""Building blocks for COB's account cards: Markdown tables and lists, money, dates.

Cards are rendered by the dashboard's shared Markdown renderer, so cell text is escaped here
(a name such as `a|b` or `**x**` must not break or style the table). Numbers are grouped the way
the dashboard groups them (en-IN, so 1,23,456), and money shows rupees first with the dollar
amount beside it, converted at the engine's own `usd_inr_rate`.
"""

import json
import re
from collections.abc import Sequence
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from callobuzz_rag.api.schemas.cob import CobLinkBlock

if TYPE_CHECKING:
    from callobuzz_rag.cob.tools.base import CobTool, ToolContext

DATA_PATH: Final = Path(__file__).resolve().parent.parent / "data" / "tool_strings.json"
MAX_ROWS: Final = 10
_LANGS: Final = ("en", "hi", "hi-Latn")
_WS = re.compile(r"\s+")
_MD = re.compile(r"([\\`*_{}\[\]<>|#~])")


@lru_cache(maxsize=1)
def _strings() -> dict[str, dict[str, str]]:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def ts(key: str, language: str, /, **values: Any) -> str:
    """A sentence or header of the cards in `language` (English for any other), placeholders
    filled. Values are inserted as given: escape user text with `esc` first."""
    entry = _strings()[key]
    text = entry.get(language if language in _LANGS else "en") or entry["en"]
    for name, value in values.items():
        text = text.replace("{" + name + "}", str(value))
    return text


def link_to(tool: "CobTool", ctx: "ToolContext", route: str) -> CobLinkBlock:
    return CobLinkBlock(label=ts(f"link_{tool.name}", ctx.language), route=route)


def esc(value: Any, limit: int = 120) -> str:
    """Text safe inside a table cell or a bold run: one line, Markdown characters escaped."""
    text = _WS.sub(" ", str(value if value is not None else "")).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return _MD.sub(r"\\\1", text)


def table(
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    more: int = 0,
    language: str = "en",
) -> str:
    """A Markdown table of at most `MAX_ROWS` rows. Cells are escaped. `more` (rows the screen
    has beyond these) adds an "and N more" line."""
    shown = list(rows)[:MAX_ROWS]
    hidden = more + max(0, len(rows) - MAX_ROWS)
    lines = [
        "| " + " | ".join(esc(h, 40) for h in headers) + " |",
        "|" + "|".join(" --- " for _ in headers) + "|",
    ]
    for row in shown:
        lines.append("| " + " | ".join(esc(c) if c != "" else "-" for c in row) + " |")
    if hidden > 0:
        lines.append("")
        lines.append(ts("and_more", language, n=num(hidden)))
    return "\n".join(lines)


def kv(pairs: Sequence[tuple[str, Any]]) -> str:
    """A bulleted key/value list. Values are escaped; pass `Raw` for text that is already
    Markdown."""
    out = []
    for key, value in pairs:
        text = value.text if isinstance(value, Raw) else esc(value)
        out.append(f"- **{esc(key, 40)}:** {text}")
    return "\n".join(out)


class Raw:
    """Text that is already safe Markdown (a number formatted here, a bold name)."""

    def __init__(self, text: str) -> None:
        self.text = text


def en_in(value: float, decimals: int = 0) -> str:
    """`1234567.5` as `12,34,567.5` (en-IN grouping)."""
    sign = "-" if value < 0 else ""
    whole, _, frac = f"{abs(value):.{decimals}f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])
    return sign + whole + ("." + frac if frac else "")


def num(value: int | float | None) -> str:
    if value is None:
        return "-"
    return en_in(float(value), 0 if float(value).is_integer() else 2)


def money(usd: float | None, rate: float | None, language: str = "en") -> str:
    """`₹1,234 ($14.80)`: rupees first like the dashboard, the dollar amount beside it. Without
    a rate only the dollar amount. A real cost too small for two decimals reads `< ₹0.01`."""
    if usd is None:
        return ts("price_unknown", language)
    if 0 < usd < 0.0001:
        dollars = "< $0.0001"
    else:
        dollars = f"${en_in(usd, 2 if usd >= 0.01 or usd == 0 else 4)}"
    if not rate:
        return dollars
    inr = usd * rate
    tiny = usd > 0 and inr < 0.005
    rupees = "< ₹0.01" if tiny else f"₹{en_in(inr, 2 if inr < 10 else 0)}"
    return f"{rupees} ({dollars})"


def parse_when(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def when(value: Any, language: str = "en") -> str:
    """`4 Oct 2026`, or `-` when there is no date."""
    moment = parse_when(value)
    if moment is None:
        return "-"
    return f"{moment.day} {moment:%b %Y}"


def used_of(used: int | None, limit: int | None, language: str = "en") -> str:
    """`212 of 300`, or `212 (no limit)`."""
    if limit is None:
        return ts("used_no_limit", language, used=num(used))
    return ts("used_of", language, used=num(used), limit=num(limit))
