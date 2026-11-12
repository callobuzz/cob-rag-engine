"""The best-first frontier of a site read (BIZ1).

The start page and the sitemap seed a priority queue ordered by `rank.score`; every fetched
page's links join it one hop deeper. The page that matters most is always read next, so a cap
(the deployer's page limit) keeps the important pages without reading the whole site first. The
order is deterministic: `(-score, depth, len(url), url)`, the same key `rank.select_pages` sorts
by. A candidate seen again is merged (shallowest depth, any menu/footer/sitemap evidence) and
re-queued under its new key.
"""

import heapq
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from callobuzz_rag.website.rank import Candidate, score
from callobuzz_rag.website.sitemap import SitemapEntry


@dataclass
class Cand:
    url: str
    depth: int
    in_nav: bool = False
    in_footer: bool = False
    in_sitemap: bool = False
    priority: float | None = None
    lastmod: datetime | None = None

    def ranked(self) -> Candidate:
        return Candidate(
            url=self.url,
            depth=self.depth,
            in_nav=self.in_nav,
            in_footer=self.in_footer,
            in_sitemap=self.in_sitemap,
            sitemap_priority=self.priority,
        )


def path_depth(url: str) -> int:
    """Path segments of a sitemap-only page (its stand-in for link hops)."""
    return len([s for s in urlsplit(url).path.split("/") if s])


class Frontier:
    def __init__(self, start_url: str) -> None:
        self.start_url = start_url
        self._cands: dict[str, Cand] = {}
        self._keys: dict[str, tuple[int, int, int, str]] = {}
        self._heap: list[tuple[int, int, int, str]] = []
        self._popped: set[str] = set()

    def __len__(self) -> int:
        """Candidates not read yet."""
        return len(self._cands) - len(self._popped)

    @property
    def known(self) -> int:
        """Distinct URLs seen so far, read or not."""
        return len(self._cands)

    def has(self, url: str) -> bool:
        return url in self._cands

    def _push(self, cand: Cand) -> None:
        ranked = cand.ranked()
        key = (-score(ranked, start_url=self.start_url), cand.depth, len(cand.url), cand.url)
        if self._keys.get(cand.url) != key:
            self._keys[cand.url] = key
            heapq.heappush(self._heap, key)

    def add(
        self,
        url: str,
        *,
        depth: int,
        in_nav: bool = False,
        in_footer: bool = False,
        in_sitemap: bool = False,
        priority: float | None = None,
        lastmod: datetime | None = None,
    ) -> bool:
        """Add or merge one candidate; True when it is new. A page already read is left alone."""
        if url in self._popped:
            return False
        old = self._cands.get(url)
        if old is None:
            cand = Cand(url, depth, in_nav, in_footer, in_sitemap, priority, lastmod)
            self._cands[url] = cand
            self._push(cand)
            return True
        old.depth = min(old.depth, depth)
        old.in_nav = old.in_nav or in_nav
        old.in_footer = old.in_footer or in_footer
        if in_sitemap:
            old.in_sitemap = True
            old.priority = priority if priority is not None else old.priority
            old.lastmod = lastmod or old.lastmod
        self._push(old)
        return False

    def seed_sitemap(self, entries: Iterable[SitemapEntry]) -> int:
        added = 0
        for entry in entries:
            added += self.add(
                entry.url,
                depth=path_depth(entry.url),
                in_sitemap=True,
                priority=entry.priority,
                lastmod=entry.lastmod,
            )
        return added

    def pop(self) -> Cand | None:
        while self._heap:
            key = heapq.heappop(self._heap)
            url = key[3]
            if url in self._popped or self._keys.get(url) != key:
                continue  # an older key of a candidate that was merged or already read
            self._popped.add(url)
            return self._cands[url]
        return None

    def get(self, url: str) -> Cand | None:
        return self._cands.get(url)

    def remaining(self) -> list[Cand]:
        """Candidates never read, best first."""
        left = [c for u, c in self._cands.items() if u not in self._popped]
        left.sort(key=lambda c: self._keys[c.url])
        return left
