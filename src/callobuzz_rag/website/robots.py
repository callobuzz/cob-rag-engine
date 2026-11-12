"""robots.txt (RFC 9309) for website learning (BIZ1).

Our group (matched on the product token, case-insensitive) replaces the `*` group; groups naming
the same agent are merged. Patterns support `*` and an end anchor `$`, the longest matching
pattern wins and `allow` wins a tie. `/robots.txt` itself is always allowed.

Status rules (RFC 9309 section 2.3.1): a 2xx answer is parsed (the first 500 KiB), a 4xx means
"no rules, everything allowed", anything else (5xx, a network error) means "disallow all" until
the next run. The crawl delay is capped at 10 seconds.
"""

import contextlib
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Final
from urllib.parse import quote

MAX_ROBOTS_BYTES: Final = 500 * 1024
MAX_CRAWL_DELAY: Final = 10.0

_PERCENT = re.compile(r"%([0-9a-fA-F]{2})")
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def _normalise(value: str) -> str:
    def fix(match: re.Match[str]) -> str:
        char = chr(int(match.group(1), 16))
        return char if char in _UNRESERVED else "%" + match.group(1).upper()

    # RFC 9309 2.2.2: raw characters are encoded before comparing, as the page paths are
    value = quote(value, safe="/%:@!$&'()*+,;=-._~?")
    return _PERCENT.sub(fix, value)


@lru_cache(maxsize=4096)
def _compile(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = ".*".join(re.escape(part) for part in body.split("*"))
    return re.compile("^" + regex + ("$" if anchored else ""))


@dataclass(frozen=True)
class Robots:
    rules: tuple[tuple[bool, str], ...]  # (allow, pattern) of the one matching group
    crawl_delay: float | None = None
    sitemaps: tuple[str, ...] = ()

    def allowed(self, path_and_query: str) -> bool:
        path = _normalise(path_and_query or "/")
        if path.split("?", 1)[0] == "/robots.txt":
            return True
        best_len = -1
        best_allow = True
        for allow, pattern in self.rules:
            if not _compile(pattern).match(path):
                continue
            length = len(pattern)
            if length > best_len or (length == best_len and allow and not best_allow):
                best_len, best_allow = length, allow
        return best_allow


def parse_robots(text: str, *, bot: str) -> Robots:
    """Parse `text`; `bot` is our product token (letters, digits, `-`)."""
    token = bot.strip().lower()
    groups: list[tuple[list[str], list[tuple[bool, str]], float | None]] = []
    sitemaps: list[str] = []
    agents: list[str] = []
    rules: list[tuple[bool, str]] = []
    delay: float | None = None
    in_rules = False

    def flush() -> None:
        if agents:
            groups.append((list(agents), list(rules), delay))

    for raw in text.lstrip("﻿").splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        name, _, value = line.partition(":")
        name, value = name.strip().lower(), value.strip()
        if name == "user-agent":
            if in_rules:
                flush()
                agents, rules, delay, in_rules = [], [], None, False
            agents.append(value.lower())
        elif name in ("allow", "disallow"):
            if not agents:
                continue
            in_rules = True
            if value:  # an empty Disallow allows everything: no rule at all
                rules.append((name == "allow", _normalise(value)))
        elif name == "crawl-delay":
            if not agents:
                continue
            in_rules = True
            with contextlib.suppress(ValueError):
                delay = max(0.0, float(value))
        elif name == "sitemap" and value:
            sitemaps.append(value)
    flush()

    def select(wanted: str) -> tuple[list[tuple[bool, str]], float | None] | None:
        picked = [g for g in groups if wanted in g[0]]
        if not picked:
            return None
        merged = [rule for g in picked for rule in g[1]]
        delays = [g[2] for g in picked if g[2] is not None]
        return merged, (max(delays) if delays else None)

    chosen = select(token) or select("*")
    rule_list, crawl_delay = chosen if chosen else ([], None)
    if crawl_delay is not None:
        crawl_delay = min(crawl_delay, MAX_CRAWL_DELAY)
    return Robots(rules=tuple(rule_list), crawl_delay=crawl_delay, sitemaps=tuple(sitemaps))


def robots_for_status(status: int, text: str | None, *, bot: str) -> Robots:
    """The rules to obey for an HTTP answer to `/robots.txt` (`status` 0 = no answer)."""
    if 200 <= status < 300:
        clipped = (text or "").encode("utf-8", "ignore")[:MAX_ROBOTS_BYTES]
        return parse_robots(clipped.decode("utf-8", "ignore"), bot=bot)
    if 400 <= status < 500:
        return Robots(rules=())
    return Robots(rules=((False, "/"),))
