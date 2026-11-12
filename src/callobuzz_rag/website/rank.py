"""Which pages matter most (BIZ1): a pure, deterministic score and the cap that uses it.

When the deployer's plan allows fewer pages than a site has, the pages a business owner would
name first win: the home page, what the menu and footer link to, pages about prices, services,
timings, contact and policies, and what the site's own sitemap rates highly. Pages buried deep or
in archives (tags, categories, dated blog posts) go last. The same input always gives the same
order: ties break by depth, then URL length, then the URL itself.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

KEYWORDS: Final = (
    "pricing", "price", "prices", "fees", "charges", "rates", "services", "service",
    "treatments", "products", "menu", "packages", "about", "contact", "faq", "faqs", "policy",
    "policies", "privacy", "refund", "returns", "shipping", "delivery", "terms", "hours",
    "timings", "location", "locations", "branches", "book", "booking", "appointment", "doctors",
    "team", "courses", "admissions",
)  # fmt: skip
_KEYWORD_SET: Final = frozenset(KEYWORDS)
_WORD: Final = re.compile(r"[^\W_]+", re.UNICODE)
_ARCHIVE: Final = re.compile(
    r"/(tag|tags|category|categories|author|authors)/|/page/\d+(/|$)|/(19|20)\d{2}(/|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Candidate:
    url: str  # canonical
    depth: int  # link hops from the start URL (sitemap-only pages: path segment count)
    in_nav: bool = False  # linked from <nav>/<header> of the start page
    in_footer: bool = False  # linked from <footer> of the start page
    in_sitemap: bool = False
    sitemap_priority: float | None = None  # 0.0-1.0; None -> 0.5 when in_sitemap
    title: str = ""


def _words(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text)}


def score(c: Candidate, *, start_url: str) -> int:
    """The importance of one page; higher first."""
    path = urlsplit(c.url).path
    total = 0
    if c.url == start_url:
        total += 1000
    if c.in_nav:
        total += 300
    if c.in_footer:
        total += 200
    if _words(path) & _KEYWORD_SET or _words(c.title) & _KEYWORD_SET:
        total += 150
    if c.in_sitemap:
        priority = 0.5 if c.sitemap_priority is None else c.sitemap_priority
        total += round(priority * 100)
    total -= 40 * c.depth
    if _ARCHIVE.search(path):
        total -= 100
    return total


def select_pages(cands: Iterable[Candidate], cap: int | None, *, start_url: str) -> list[Candidate]:
    """The `cap` most important candidates in order (`None` = all of them)."""
    ranked = sorted(
        cands, key=lambda c: (-score(c, start_url=start_url), c.depth, len(c.url), c.url)
    )
    return ranked if cap is None else ranked[: max(cap, 0)]
