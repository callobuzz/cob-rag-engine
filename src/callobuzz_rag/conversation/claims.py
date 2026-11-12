# src/callobuzz_rag/conversation/claims.py
"""Gate 2 (design §5.2): checkable facts in an answer must be backed by its sources.

`extract_claims` finds money, quantities, percentages, dates, times, phones, e-mails,
URLs and named entities and normalises each to a comparable value. `ClaimIndex` holds
what the sources state. `SentenceGate` sits between the model's stream and the client:
it releases a sentence only once it ends and its claims are checked, so an unsupported
fact never reaches the screen. Named entities are Latin-script Title Case runs only;
Devanagari has no capitalisation, so Hindi names are not checked (documented limit);
Hindi numbers, prices, dates and contacts are.

What counts as a source (the support boundary): the passages retrieved for this answer,
the business card and the assistant's own configuration (its name, the business name, the
disclosure line and the configured topics). Nothing else ever supports a value. The
visitor's question supports names only: echoing a product the visitor named is not a
business fact, and a price, date or number the visitor typed is never confirmed by it.
Citations are not required per row: models cite once per table or paragraph (or once after
it, or not at all), so every value is looked up in all the retrieved passages, never
outside them.

Tables state units in their header: `| Weight (kg) | Price (Rs) |` over `| 1.4 | 14,999 |`
says 1.4 kg and Rs 14,999. `table_claims` reads a cell in a typed column as that kind, in
the sources and in the answer alike, so a bare price in an answer's price column is
checked (a bait value there is removed) and a bare cell in a source supports it. A number
right after a price word ("price 14,999", "कीमत 14,999") is a price too.
"""

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from callobuzz_rag.text.tokenize import AFTER_WORD, WORD_CLASS, normalize, tokenize

if TYPE_CHECKING:
    from callobuzz_rag.conversation.safety import EchoFilter


class ClaimKind(StrEnum):
    MONEY = "money"
    QUANTITY = "quantity"
    PERCENT = "percent"
    DATE = "date"
    TIME = "time"
    HOUR = "hour"
    PHONE = "phone"
    EMAIL = "email"
    URL = "url"
    ENTITY = "entity"


EXPLAINER_KINDS = frozenset(
    {
        ClaimKind.MONEY,
        ClaimKind.PHONE,
        ClaimKind.EMAIL,
        ClaimKind.URL,
        ClaimKind.DATE,
        ClaimKind.TIME,
        ClaimKind.HOUR,
    }
)


@dataclass(frozen=True)
class Claim:
    kind: ClaimKind
    value: str
    raw: str


# Devanagari, Bengali, Gurmukhi, Gujarati, Tamil, Telugu, Kannada and Malayalam digits.
_INDIC_ZEROS = (0x0966, 0x09E6, 0x0A66, 0x0AE6, 0x0BE6, 0x0C66, 0x0CE6, 0x0D66)
_DEVANAGARI_DIGITS = {zero + d: ord("0") + d for zero in _INDIC_ZEROS for d in range(10)}
_MULT = {
    "lakh": 100_000,
    "lakhs": 100_000,
    "lac": 100_000,
    "crore": 10_000_000,
    "crores": 10_000_000,
    "cr": 10_000_000,
    "k": 1_000,
}
_NUM = r"\d[\d,]*(?:\.\d+)?"
_RU = rf"रु(?:\.|(?![{WORD_CLASS}]))"  # the short form "रु" / "रु.", never "रुकें"
_MONEY = re.compile(
    rf"(?:₹|\brs\.?|\binr\b|\brupees?\b|रुपये|रुपए|{_RU})\s*({_NUM})\s*"
    rf"(lakhs?|lac|crores?|cr|k)?\b"
    rf"|({_NUM})\s*(lakhs?|lac|crores?|cr|k)?\s*"
    rf"(?:rupees?\b|\brs\b\.?|/-|रुपये|रुपए|रुपया|{_RU}|रू)",
    re.IGNORECASE,
)
# A number right after a price word: "price 14,999", "MRP: 8,999", "कीमत 14,999".
_PRICE_WORDS = ("price", "prices", "priced", "cost", "costs", "mrp", "fee", "fees", "charges")
_PRICE_WORDS_HI = ("कीमत", "क़ीमत", "मूल्य", "शुल्क", "keemat", "kimat", "qeemat")
_PRICE_CUE = re.compile(
    rf"(?<![{WORD_CLASS}])(?:{'|'.join(_PRICE_WORDS + _PRICE_WORDS_HI)})(?![{WORD_CLASS}])"
    rf"[^\d\n|]{{0,20}}?(?<![\d.,])({_NUM})\s*(lakhs?|lac|crores?|cr|k)?{AFTER_WORD}",
    re.IGNORECASE,
)
_UNITS: dict[str, tuple[str, ...]] = {
    "day": ("day", "days", "दिन", "din"),
    "hour": ("hour", "hours", "hr", "hrs", "घंटे", "घंटा", "ghante", "ghanta"),
    "minute": ("minute", "minutes", "min", "mins", "मिनट"),
    "week": ("week", "weeks", "हफ्ते", "हफ़्ते", "सप्ताह", "hafte"),
    "month": ("month", "months", "महीने", "महीना", "mahine", "mahina"),
    "year": ("year", "years", "yr", "yrs", "साल", "saal", "varsh"),
    "km": ("km", "kms", "kilometre", "kilometres", "kilometer", "kilometers", "किमी", "किलोमीटर"),
    "kg": ("kg", "kgs", "kilogram", "kilograms", "kilo", "kilos", "किलो", "किलोग्राम", "किग्रा"),
}
_UNIT_OF = {alias: canon for canon, aliases in _UNITS.items() for alias in aliases}
_QTY = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:(?:-|–|to|se)\s*(\d+(?:\.\d+)?)\s*)?("  # noqa: RUF001
    + "|".join(sorted((re.escape(a) for a in _UNIT_OF), key=len, reverse=True))
    + rf"){AFTER_WORD}",
    re.IGNORECASE,
)
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent\b|per cent\b|प्रतिशत)", re.IGNORECASE)
_MONTH_NAMES = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MONTHS = {m: i for i, m in enumerate(_MONTH_NAMES, 1)}
_MON = "(" + "|".join(_MONTH_NAMES) + r")[a-z]*\.?"
_DMY = re.compile(r"(?<!\d)(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})(?!\d)")
_D_MON = re.compile(
    rf"(?<!\d)(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MON}(?:,?\s+(\d{{4}}))?", re.IGNORECASE
)
_MON_D = re.compile(
    rf"\b{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?(?!\d)", re.IGNORECASE
)
_TIME_12 = re.compile(r"(?<![\d:])(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)", re.IGNORECASE)
_TIME_24 = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
_HOUR = re.compile(r"(?<!\d)(\d{1,2})\s*(?:baje|बजे)", re.IGNORECASE)
_PHONE = re.compile(r"(?<![\d+])(?:\+?91[\s-]?|0)?([6-9]\d{4}[\s-]?\d{5})(?!\d)")
_LANDLINE = re.compile(r"(?<!\d)(1800[\s-]?\d{3}[\s-]?\d{4}|0\d{2,4}[\s-]?\d{6,8})(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL = re.compile(r"\b(?:https?://|www\.)[^\s<>()\[\]\"']+", re.IGNORECASE)
_TITLE = re.compile(r"[A-Z][a-z]+|[A-Z]{2,}")
_CONNECTORS = {"of", "and", "&"}
_STOPWORDS = {
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "the",
    "our",
    "we",
    "you",
    "your",
    "please",
    "yes",
    "no",
    "hi",
    "hello",
    "thank",
    "thanks",
    "for",
    "if",
    "in",
    "on",
    "at",
    "this",
    "that",
    "it",
    "i",
    "sure",
    "general",
    "information",
    "note",
    "also",
    "however",
    "unfortunately",
    "sorry",
    "visit",
    "meet",
    "call",
    "book",
    "contact",
}
_CITE_FORMS = re.compile(r"【\s*(\d{1,2})\s*】|\[\^(\d{1,2})\]")


def normalize_digits(text: str) -> str:
    return text.translate(_DEVANAGARI_DIGITS)


def normalize_citations(text: str) -> str:
    """Rewrite 【n】 and [^n] as [n]. A bare `^n` is left to the renderers: it is a citation
    only after a letter or punctuation and only when source n exists, so `2^10` and `x^7`
    survive."""
    return _CITE_FORMS.sub(lambda m: f"[{m.group(1) or m.group(2)}]", text)


def _paise(number: str, mult: str | None) -> str:
    value = float(number.replace(",", "")) * _MULT.get((mult or "").lower(), 1)
    return str(round(value * 100))


def _num(value: str) -> str:
    f = float(value)
    return str(int(f)) if f.is_integer() else str(f)


def _hhmm(hour: int, minute: int, meridiem: str | None) -> str:
    m = (meridiem or "").lower().replace(".", "")
    if m == "pm" and hour < 12:
        hour += 12
    if m == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


def _date(day: str, month: int, year: str | None) -> str:
    y = None if not year else (year if len(year) == 4 else f"20{year}")
    return f"{int(day)}-{month}" + (f"-{y}" if y else "")


def _url_value(raw: str) -> str:
    url = raw.rstrip(".,;:!?)").lower()
    url = re.sub(r"^https?://", "", url)
    url = re.sub(r"^www\.", "", url)
    return url.rstrip("/")


def extract_claims(
    text: str, *, stoplist: frozenset[str] = frozenset(), kinds: frozenset[ClaimKind] | None = None
) -> list[Claim]:
    text = normalize_digits(text)
    taken: list[tuple[int, int]] = []
    out: list[Claim] = []

    def add(kind: ClaimKind, value: str, start: int, end: int, raw: str) -> bool:
        if any(start < e and end > s for s, e in taken):
            return False
        taken.append((start, end))
        if kinds is None or kind in kinds:
            out.append(Claim(kind, value, raw))
        return True

    for m in _EMAIL.finditer(text):
        add(ClaimKind.EMAIL, m.group(0).lower(), m.start(), m.end(), m.group(0))
    for m in _URL.finditer(text):
        add(ClaimKind.URL, _url_value(m.group(0)), m.start(), m.end(), m.group(0))
    for m in _PHONE.finditer(text):
        add(ClaimKind.PHONE, re.sub(r"\D", "", m.group(1))[-10:], m.start(), m.end(), m.group(0))
    for m in _LANDLINE.finditer(text):
        add(ClaimKind.PHONE, re.sub(r"\D", "", m.group(1)), m.start(), m.end(), m.group(0))
    for m in _MONEY.finditer(text):
        number, mult = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        add(ClaimKind.MONEY, _paise(number, mult), m.start(), m.end(), m.group(0))
    for m in _PCT.finditer(text):
        add(ClaimKind.PERCENT, _num(m.group(1)), m.start(), m.end(), m.group(0))
    for m in _DMY.finditer(text):
        add(
            ClaimKind.DATE,
            _date(m.group(1), int(m.group(2)), m.group(3)),
            m.start(),
            m.end(),
            m.group(0),
        )
    for m in _D_MON.finditer(text):
        add(
            ClaimKind.DATE,
            _date(m.group(1), _MONTHS[m.group(2).lower()[:3]], m.group(3)),
            m.start(),
            m.end(),
            m.group(0),
        )
    for m in _MON_D.finditer(text):
        add(
            ClaimKind.DATE,
            _date(m.group(2), _MONTHS[m.group(1).lower()[:3]], m.group(3)),
            m.start(),
            m.end(),
            m.group(0),
        )
    for m in _TIME_12.finditer(text):
        add(
            ClaimKind.TIME,
            _hhmm(int(m.group(1)), int(m.group(2) or 0), m.group(3)),
            m.start(),
            m.end(),
            m.group(0),
        )
    for m in _TIME_24.finditer(text):
        add(
            ClaimKind.TIME,
            _hhmm(int(m.group(1)), int(m.group(2)), None),
            m.start(),
            m.end(),
            m.group(0),
        )
    for m in _HOUR.finditer(text):
        add(ClaimKind.HOUR, str(int(m.group(1))), m.start(), m.end(), m.group(0))
    for m in _QTY.finditer(text):
        unit = _UNIT_OF[m.group(3).lower()]
        if (
            add(ClaimKind.QUANTITY, f"{_num(m.group(1))} {unit}", m.start(), m.end(), m.group(0))
            and m.group(2)
            and (kinds is None or ClaimKind.QUANTITY in kinds)
        ):
            out.append(Claim(ClaimKind.QUANTITY, f"{_num(m.group(2))} {unit}", m.group(0)))
    for m in _PRICE_CUE.finditer(text):
        if sum(c.isdigit() for c in m.group(1)) >= 3:  # "price for 2 people" is no price
            add(ClaimKind.MONEY, _paise(m.group(1), m.group(2)), m.start(1), m.end(), m.group(0))
    for value, start, end, raw in _entities(text, stoplist):
        add(ClaimKind.ENTITY, value, start, end, raw)
    return out


Column = tuple[ClaimKind, str | None] | None
_MONEY_HEADER_WORDS = frozenset(
    normalize(w)
    for w in (
        *_PRICE_WORDS,
        *_PRICE_WORDS_HI,
        "rs",
        "inr",
        "rupee",
        "rupees",
        "amount",
        "रुपये",
        "रुपए",
        "रुपया",
        "रु",
        "रू",
        "दाम",
        "daam",
    )
)
_PERCENT_HEADER_WORDS = frozenset({"percent", "percentage", normalize("प्रतिशत")})
_UNIT_KEYS = {normalize(alias): canon for alias, canon in _UNIT_OF.items()}
_CELL_NUMBER = re.compile(
    rf"(?<![\w.,/:-])({_NUM})\s*(lakhs?|lac|crores?|cr|k)?(?![\w/:]|[.,]\d|-\w)",
    re.IGNORECASE,
)


def _cells(line: str) -> list[str]:
    cells = line.strip().split("|")
    if cells and not cells[0].strip():
        cells = cells[1:]
    if cells and not cells[-1].strip():
        cells = cells[:-1]
    return cells


def column_kinds(header: str) -> list[Column]:
    """What each column of a Markdown table header says its cells are: money ("Price (Rs)",
    "कीमत", "₹"), a percentage, a quantity in a unit ("Weight (kg)", "वज़न (किलो)"), or
    nothing."""
    out: list[Column] = []
    for cell in _cells(normalize_digits(header)):
        low = cell.lower()
        words = set(tokenize(low))
        if "₹" in low or words & _MONEY_HEADER_WORDS:
            out.append((ClaimKind.MONEY, None))
        elif "%" in low or words & _PERCENT_HEADER_WORDS:
            out.append((ClaimKind.PERCENT, None))
        else:
            unit = next((_UNIT_KEYS[w] for w in tokenize(low) if w in _UNIT_KEYS), None)
            out.append((ClaimKind.QUANTITY, unit) if unit else None)
    return out


def cell_claims(row: str, columns: list[Column]) -> list[Claim]:
    """Bare numbers in the typed columns of a table row, read as the header types them. A
    cell that already states its kind ("₹14,999", "1.4 kg") is left to `extract_claims`."""
    out: list[Claim] = []
    for cell, column in zip(_cells(normalize_digits(row)), columns, strict=False):
        if column is None:
            continue
        kind, unit = column
        if any(c.kind == kind for c in extract_claims(cell)):
            continue
        for m in _CELL_NUMBER.finditer(cell):
            number, mult = m.group(1), m.group(2)
            if kind == ClaimKind.MONEY:
                out.append(Claim(kind, _paise(number, mult), m.group(0)))
            elif kind == ClaimKind.PERCENT:
                out.append(Claim(kind, _num(number.replace(",", "")), m.group(0)))
            elif not mult:
                out.append(Claim(kind, f"{_num(number.replace(',', ''))} {unit}", m.group(0)))
    return out


def table_claims(text: str) -> list[Claim]:
    """The typed cells of every Markdown table in `text` (header, delimiter, rows)."""
    out: list[Claim] = []
    lines = text.splitlines()
    columns: list[Column] | None = None
    for i, line in enumerate(lines):
        if "|" not in line:
            columns = None
            continue
        if _is_table_delimiter(line.strip()):
            continue
        if columns is not None:
            out.extend(cell_claims(line, columns))
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if _is_table_delimiter(nxt):
            columns = column_kinds(line)
    return out


def _entities(text: str, stoplist: frozenset[str]) -> Iterator[tuple[str, int, int, str]]:
    """Runs of ≥ 2 Title Case / ALLCAPS words (connectors "of", "and", "&" allowed inside),
    trimmed of stopwords at either end, minus anything in `stoplist`."""
    run: list[re.Match[str]] = []

    def emit(words: list[re.Match[str]]) -> Iterator[tuple[str, int, int, str]]:
        def edge(w: re.Match[str]) -> bool:
            low = w.group(0).lower().strip(".")
            return low in _STOPWORDS or low in _CONNECTORS

        while words and edge(words[0]):
            words = words[1:]
        while words and edge(words[-1]):
            words = words[:-1]
        content = [w for w in words if w.group(0).lower() not in _CONNECTORS]
        if len(content) < 2:
            return
        value = " ".join(w.group(0).strip(".").lower() for w in words)
        if any(value in s or s in value for s in stoplist):
            return
        yield value, words[0].start(), words[-1].end(), text[words[0].start() : words[-1].end()]

    for tok in re.finditer(r"[A-Za-z][A-Za-z'&-]*\.?|&", text):
        word = tok.group(0).rstrip(".")
        is_title = bool(_TITLE.fullmatch(word))
        gap = text[run[-1].end() : tok.start()] if run else ""
        contiguous = not run or (gap in (" ", "  ") and not run[-1].group(0).endswith("."))
        if run and contiguous and (is_title or word.lower() in _CONNECTORS):
            run.append(tok)
            continue
        if run:
            yield from emit(run)
        run = [tok] if is_title else []
    if run:
        yield from emit(run)


class ClaimIndex:
    """What the sources say, normalised for lookup. `texts` support every kind of claim;
    `names_only` (the visitor's question) supports names and nothing else."""

    def __init__(self, texts: Iterable[str], *, names_only: Iterable[str] = ()) -> None:
        texts = list(texts)
        joined = "\n".join(texts)
        self._values: dict[ClaimKind, set[str]] = {k: set() for k in ClaimKind}
        for claim in extract_claims(joined):
            self._values[claim.kind].add(claim.value)
        for text in texts:
            for claim in table_claims(text):
                self._values[claim.kind].add(claim.value)
        self._text = _flat(joined)
        self._names = _flat("\n".join(names_only))

    def supports(self, claim: Claim) -> bool:
        values = self._values[claim.kind]
        if claim.kind == ClaimKind.ENTITY:
            return claim.value in self._text or claim.value in self._names
        if claim.kind == ClaimKind.URL:
            host, _, path = claim.value.partition("/")
            for known in self._values[ClaimKind.URL]:
                k_host, _, k_path = known.partition("/")
                if k_host == host and (not path or k_path == path or k_path.startswith(path + "/")):
                    return True
            return False
        if claim.kind == ClaimKind.DATE:
            return claim.value in values or any(v.startswith(claim.value + "-") for v in values)
        if claim.kind == ClaimKind.HOUR:
            hour = int(claim.value)
            hours = {int(v.split(":")[0]) for v in self._values[ClaimKind.TIME]}
            return hour in hours or hour + 12 in hours or claim.value in values
        if claim.kind == ClaimKind.TIME:
            hour12 = str(int(claim.value.split(":")[0]) % 12 or 12)
            return claim.value in values or hour12 in self._values[ClaimKind.HOUR]
        return claim.value in values


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", normalize_digits(text)).lower()


@dataclass
class SentenceVerdict:
    sentence: str
    claims: list[tuple[Claim, bool]]
    action: Literal["kept", "removed", "flagged"]


_ABBREV = {"dr", "mr", "mrs", "ms", "rs", "no", "st", "vs", "eg", "ie", "etc", "approx", "prof"}
_BOUNDARY = re.compile(r"(?<=[.!?।])\s+|\n")
# "1." or "12." alone on a line is a list marker, not the end of a sentence.
_LIST_MARKER = re.compile(r"\s*\d{1,3}[.)]")
_TABLE_DELIMITER = re.compile(r"\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*")


def _is_table_row(line: str) -> bool:
    return line.startswith("|")


def _is_table_delimiter(line: str) -> bool:
    return "|" in line and _TABLE_DELIMITER.fullmatch(line) is not None


# A sentence that talks about updating: only there may the source's update date stand as a date
# (TV5). English, Hindi and Hinglish; `updat\w*` covers "update", "updated", "updating".
_ABOUT_UPDATE = re.compile(
    r"\bupdat\w*|\blast\s+(?:read|checked|verified)\b|\b(?:modified|revised)\b|अपडेट|अद्यतन",
    re.IGNORECASE,
)


@dataclass
class SentenceGate:
    """Checks the answer a sentence at a time. Markdown-aware: a list item is one unit (its
    "1." marker is never a boundary), a code fence line is one unit, and a table is held
    (header + delimiter) until its first data row passes, then released with it; a table
    whose every data row is removed disappears entirely. A table's header types its columns
    (`column_kinds`), so a bare number under "Price (Rs)" is checked as a price.

    `flush(truncated=True)` (the model hit its token limit) drops the cut-off tail: a half
    sentence, a half table row or a table header with no rows never reaches the visitor;
    the answer ends on its last complete unit and `cut` keeps what was dropped."""

    index: ClaimIndex
    mode: Literal["enforce", "warn", "off"] = "enforce"
    echo: "EchoFilter | None" = None
    stoplist: frozenset[str] = frozenset()
    kinds: frozenset[ClaimKind] | None = None
    # TV5: what a source says about when it was updated. It supports a date claim only in a
    # sentence about updating, never a date anywhere else in the answer.
    update_index: ClaimIndex | None = None
    verdicts: list[SentenceVerdict] = field(default_factory=list)
    removed: int = 0
    flagged: int = 0
    released: str = ""
    cut: str = ""
    _buffer: str = ""
    _in_fence: bool = False
    _table: Literal["head", "delim", "body", "dead"] | None = None
    _held: list[str] = field(default_factory=list)
    _columns: list[Column] = field(default_factory=list)
    _header_cells: int = 0

    def feed(self, delta: str) -> list[str]:
        self._buffer += delta
        out: list[str] = []
        while (cut := self._next_boundary()) is not None:
            unit, self._buffer = self._buffer[:cut], self._buffer[cut:]
            out.extend(self._handle(unit))
        self.released += "".join(out)
        return out

    def flush(self, *, truncated: bool = False) -> list[str]:
        rest, self._buffer = self._buffer, ""
        if truncated and rest.strip() and not self._complete(rest):
            self.cut, rest = rest, ""
        out = self._handle(rest) if rest.strip() else ([rest] if rest else [])
        if self._table == "head" and not truncated:  # a lone pipe line was never a table
            out.extend(self._held)
        elif self._table in ("head", "delim"):
            self.cut = "".join(self._held) + self.cut
        self._table, self._held = None, []
        self.released += "".join(out)
        return out

    def _complete(self, rest: str) -> bool:
        """Whether text left at a length stop is whole: a table row with every column of
        its table, or a sentence that reached its full stop."""
        line = rest.strip()
        if self._in_fence or line.startswith("```"):
            return False
        if _is_table_row(line):
            if self.mode == "off":
                return line.endswith("|")
            return (
                self._table in ("delim", "body")
                and line.endswith("|")
                and len(_cells(line)) >= self._header_cells
            )
        return line[-1] in ".!?।" and not _LIST_MARKER.fullmatch(line[line.rfind("\n") + 1 :])

    def _next_boundary(self) -> int | None:
        buf = self._buffer
        if self._in_fence or buf.lstrip().startswith("```"):
            newline = buf.find("\n")
            return None if newline < 0 else newline + 1
        for m in _BOUNDARY.finditer(buf):
            before = buf[: m.start()]
            if m.group(0) != "\n":
                word = re.findall(r"(\w+)\.$", before)
                if word and word[-1].lower() in _ABBREV:
                    continue
                if _LIST_MARKER.fullmatch(before[before.rfind("\n") + 1 :]):
                    continue
            return m.end()
        return None

    def _handle(self, unit: str) -> list[str]:
        line = unit.strip()
        if self._in_fence or line.startswith("```"):
            if line.startswith("```"):
                self._in_fence = not self._in_fence
            out = self._leave_table()
            piece = self._check(unit)
            return out + ([piece] if piece else [])
        if self.mode != "off" and _is_table_row(line):
            return self._table_row(unit, line)
        out = self._leave_table()
        piece = self._check(unit)
        return out + ([piece] if piece else [])

    def _leave_table(self) -> list[str]:
        """A non-table line ends a table; a lone held pipe line is released as text."""
        out = list(self._held) if self._table == "head" else []
        self._table, self._held = None, []
        return out

    def _table_row(self, unit: str, line: str) -> list[str]:
        if self._table == "dead":
            return []
        if self._table is None:
            piece = self._check(unit)
            if not piece:  # an unsupported header: the table goes with it
                self._table = "dead"
                return []
            self._table, self._held = "head", [piece]
            self._columns, self._header_cells = column_kinds(line), len(_cells(line))
            return []
        if self._table == "head":
            if _is_table_delimiter(line):
                self._held.append(unit)
                self._table = "delim"
                return []
            out = list(self._held)  # not a table after all
            self._table, self._held = None, []
            return out + self._table_row(unit, line)
        piece = self._check(unit, self._columns)
        if self._table == "delim":
            if not piece:
                return []
            out = ["".join(self._held), piece]
            self._table, self._held = "body", []
            return out
        return [piece] if piece else []

    def _supports(self, claim: Claim, about_update: bool) -> bool:
        if self.index.supports(claim):
            return True
        return (
            about_update
            and claim.kind == ClaimKind.DATE
            and self.update_index is not None
            and self.update_index.supports(claim)
        )

    def _check(self, sentence: str, columns: list[Column] | None = None) -> str:
        sentence = normalize_citations(sentence)
        if self.mode == "off" or not sentence.strip():
            return sentence
        claims = extract_claims(sentence, stoplist=self.stoplist, kinds=self.kinds)
        if columns:
            claims += [
                c
                for c in cell_claims(sentence, columns)
                if self.kinds is None or c.kind in self.kinds
            ]
        about_update = self.update_index is not None and _ABOUT_UPDATE.search(sentence) is not None
        results = [(c, self._supports(c, about_update)) for c in claims]
        leaked = self.echo is not None and self.echo.leaks(sentence)
        if not leaked and all(ok for _, ok in results):
            if results:
                self.verdicts.append(SentenceVerdict(sentence, results, "kept"))
            return sentence
        if self.mode == "warn" and not leaked:
            self.flagged += 1
            self.verdicts.append(SentenceVerdict(sentence, results, "flagged"))
            return sentence
        self.removed += 1
        self.verdicts.append(SentenceVerdict(sentence, results, "removed"))
        return ""
