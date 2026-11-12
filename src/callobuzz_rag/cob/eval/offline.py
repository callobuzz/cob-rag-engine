"""The LLM-free COB eval: runs in CI and needs no network, no model and no database.

Retrieval is an in-memory BM25 over COB's own records (`cob.kb.records_for`, tokenised like the
engine's keyword search), filtered by the role's audiences; routing is COB's rules with no model,
so only the rules can place a message. Five bars, four of them absolute:

- role filter: no item ever retrieves a section above its role (100 %);
- negative items: a question only the administrator may have answered must reach the
  administrator-only text (the probe would fire) and never the allowed top five (100 %);
- rules routing: every item marked `routing: rules` is placed by the rules alone (100 %);
- platform docs: a fixture administrator-only platform doc (COB9) is never retrieved for any
  role below the administrator, and is retrieved for the administrator (100 %);
- doc hit at 5: the expected section is in the top five (>= 0.80, Hindi and Hinglish >= 0.70).
"""

import asyncio
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Final

from callobuzz_rag.cob.eval.items import EvalItem
from callobuzz_rag.cob.kb import parse_external_id, records_for
from callobuzz_rag.cob.types import Audience, CobRole, DocsIndex, allowed_audiences
from callobuzz_rag.retrieval.sparse import SparseConfig, analyze

K1: Final = 1.2
B: Final = 0.75
TOP_K: Final = 5
PROBE_K: Final = 3
HIT_BAR: Final = 0.80
HIT_BAR_INDIC: Final = 0.70
_CONFIG: Final = SparseConfig()
_ALL_AUDIENCES: Final = tuple(a.value for a in Audience)

# COB9: platform docs seeded for the role-leak check. The marker words appear nowhere else.
PLATFORM_MARKER: Final = "quillfeather zebraquartz"
PLATFORM_FIXTURES: Final = (
    ("platform/fixture-admin", Audience.ADMIN.value, f"Internal margins memo {PLATFORM_MARKER}."),
    (
        "platform/fixture-customer",
        Audience.CUSTOMER.value,
        "Support hours are nine to five on weekdays.",
    ),
)


@dataclass(frozen=True)
class Hit:
    slug: str
    anchor: str
    audience: str
    text: str
    score: float


class Bm25Index:
    """BM25 over every section of the docs index (k1 1.2, b 0.75, like the engine's keyword
    search), with the audience filter applied at query time."""

    def __init__(
        self, index: DocsIndex, *, platform_docs: tuple[tuple[str, str, str], ...] = ()
    ) -> None:
        self._rows: list[tuple[str, str, str, str, Counter[str], int]] = []
        document_frequency: Counter[str] = Counter()
        entries: list[tuple[str, str, str, str, str]] = []
        for record in records_for(index):
            parsed = parse_external_id(record.id)
            if parsed is None:
                continue
            slug, anchor = parsed
            audience = str(record.metadata["audience"])
            entries.append((slug, anchor, audience, record.text, f"{record.title}\n{record.text}"))
        for slug, audience, text in platform_docs:  # COB9 fixtures
            entries.append((slug, "", audience, text, text))
        for slug, anchor, audience, text, indexed in entries:
            terms = analyze(indexed, _CONFIG)
            counts = Counter(terms)
            document_frequency.update(counts.keys())
            self._rows.append((slug, anchor, audience, text, counts, len(terms)))
        n = len(self._rows)
        self._avg = (sum(r[5] for r in self._rows) / n) if n else 1.0
        self._idf = {
            term: math.log(1 + (n - df + 0.5) / (df + 0.5))
            for term, df in document_frequency.items()
        }

    def search(self, query: str, audiences: list[str], k: int) -> list[Hit]:
        terms = set(analyze(query, _CONFIG))
        allowed = set(audiences)
        scored: list[tuple[float, int]] = []
        for position, (_, _, audience, _, counts, length) in enumerate(self._rows):
            if audience not in allowed:
                continue
            score = 0.0
            for term in terms:
                tf = counts.get(term, 0)
                if not tf:
                    continue
                norm = tf + K1 * (1 - B + B * length / self._avg)
                score += self._idf[term] * tf * (K1 + 1) / norm
            if score > 0:
                scored.append((score, position))
        scored.sort(key=lambda s: (-s[0], s[1]))
        out: list[Hit] = []
        for score, position in scored[:k]:
            slug, anchor, audience, text, _, _ = self._rows[position]
            out.append(Hit(slug, anchor, audience, text, score))
        return out

    def audience_of(self, slug: str, anchor: str | None) -> set[str]:
        """The audiences of a page's sections (of one section when `anchor` is given)."""
        return {
            row[2] for row in self._rows if row[0] == slug and (anchor is None or row[1] == anchor)
        }


@dataclass
class OfflineReport:
    items: int = 0
    answerable: int = 0
    answerable_indic: int = 0
    hit_at_5: float = 0.0
    hit_at_5_indic: float = 0.0
    negatives: int = 0
    rules_items: int = 0
    role_leaks: list[tuple[str, str]] = field(default_factory=list)
    platform_leaks: list[tuple[str, str]] = field(default_factory=list)
    negatives_failed: list[tuple[str, str]] = field(default_factory=list)
    routing_failed: list[tuple[str, str]] = field(default_factory=list)
    misses: list[str] = field(default_factory=list)
    misses_indic: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            not self.role_leaks
            and not self.platform_leaks
            and not self.negatives_failed
            and not self.routing_failed
            and self.hit_at_5 >= HIT_BAR
            and self.hit_at_5_indic >= HIT_BAR_INDIC
        )

    def summary(self) -> dict[str, Any]:
        """The numbers worth storing and printing."""
        return {
            "items": self.items,
            "hit_at_5": round(self.hit_at_5, 4),
            "hit_at_5_indic": round(self.hit_at_5_indic, 4),
            "role_leaks": len(self.role_leaks),
            "platform_leaks": len(self.platform_leaks),
            "negatives_failed": len(self.negatives_failed),
            "routing_failed": len(self.routing_failed),
            "passed": self.passed,
        }


def hit_matches(item: EvalItem, hit: Hit | tuple[str, str]) -> bool:
    slug, anchor = (hit.slug, hit.anchor) if isinstance(hit, Hit) else hit
    expect = item.expect
    return slug == expect.slug and (expect.anchor is None or anchor == expect.anchor)


def check_negative(item: EvalItem, bm25: Bm25Index) -> str | None:
    """Why a negative role item fails, or None when it passes."""
    expect = item.expect
    allowed = allowed_audiences(item.cob_role)
    higher = [a for a in _ALL_AUDIENCES if a not in allowed]
    if expect.slug is None:
        return "a negative item names the section that must not be reachable"
    if not (bm25.audience_of(expect.slug, expect.anchor) - set(allowed)):
        return "the expected section is not above the role"
    reachable = bm25.search(item.search_text, allowed, TOP_K)
    if any(hit_matches(item, hit) for hit in reachable):
        return "the section is reachable for the role"
    probe = bm25.search(item.search_text, higher, PROBE_K)
    if not any(hit_matches(item, hit) for hit in probe):
        return "the administrator-only probe would not find the section"
    for needle in expect.must_not_include:
        if any(needle in hit.text for hit in reachable):
            return f"{needle!r} appears in text the role can retrieve"
    return None


def check_platform_leaks(index: DocsIndex) -> list[tuple[str, str]]:
    """(role, why) for every role that retrieves the administrator-only platform fixture when it
    should not, or that cannot retrieve it when it should."""
    bm25 = Bm25Index(index, platform_docs=PLATFORM_FIXTURES)
    problems: list[tuple[str, str]] = []
    for role in CobRole:
        allowed = allowed_audiences(role)
        found = any(
            hit.slug == PLATFORM_FIXTURES[0][0]
            for hit in bm25.search(PLATFORM_MARKER, allowed, TOP_K)
        )
        if Audience.ADMIN.value in allowed and not found:
            problems.append((role.value, "cannot reach the administrator platform doc"))
        if Audience.ADMIN.value not in allowed and found:
            problems.append((role.value, "reaches the administrator platform doc"))
    return problems


async def _route(item: EvalItem, rules: Any) -> str | None:
    from callobuzz_rag.cob.router import CobRouter

    decision = await CobRouter(None, rules=rules).decide(item.question, [], item.cob_role, None)
    expect = item.expect
    if decision.source != "rules":
        return f"routed by {decision.source}, not by the rules"
    if expect.behaviour == "decline" and expect.tool:
        if decision.tool != expect.tool or decision.refusal is None:
            return f"expected a refusal for {expect.tool}, got {decision.tool}/{decision.refusal}"
        return None
    if decision.tool != expect.tool:
        return f"expected tool {expect.tool}, got {decision.tool}"
    if decision.intent != expect.intent:
        return f"expected intent {expect.intent}, got {decision.intent}"
    return None


def run_offline(
    index: DocsIndex, items: list[EvalItem], *, rules: Any | None = None
) -> OfflineReport:
    """Grade `items` against the docs `index` without a model or a network (see module doc)."""
    import callobuzz_rag.cob.tools  # noqa: F401  (registers the tools the router names)
    from callobuzz_rag.cob.router import load_rules

    rules = rules or load_rules()
    bm25 = Bm25Index(index)
    report = OfflineReport(items=len(items))
    report.platform_leaks = check_platform_leaks(index)
    hits = hits_indic = 0
    for item in items:
        allowed = allowed_audiences(item.cob_role)
        top = bm25.search(item.search_text, allowed, TOP_K)
        for hit in top:
            # Graded against the docs index, not the row's own tag (which is what filtered it).
            page = index.pages.get(hit.slug)
            sections = page.sections if page is not None else []
            audiences = {s.audience.value for s in sections if s.anchor == hit.anchor}
            if not audiences or audiences - set(allowed):
                report.role_leaks.append((item.id, f"{hit.slug}#{hit.anchor}"))
        if item.is_negative:
            report.negatives += 1
            why = check_negative(item, bm25)
            if why:
                report.negatives_failed.append((item.id, why))
        elif item.expect.behaviour == "answer" and item.expect.slug:
            report.answerable += 1
            report.answerable_indic += item.is_indic
            if any(hit_matches(item, hit) for hit in top):
                hits += 1
                hits_indic += item.is_indic
            else:
                report.misses.append(item.id)
                if item.is_indic:
                    report.misses_indic.append(item.id)
        if item.routing == "rules":
            report.rules_items += 1
            why = asyncio.run(_route(item, rules))
            if why:
                report.routing_failed.append((item.id, why))
    report.hit_at_5 = hits / report.answerable if report.answerable else 1.0
    report.hit_at_5_indic = hits_indic / report.answerable_indic if report.answerable_indic else 1.0
    return report
