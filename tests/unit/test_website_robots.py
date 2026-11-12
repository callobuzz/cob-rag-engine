import pytest

from callobuzz_rag.website.robots import parse_robots, robots_for_status

ROBOTS = """
User-agent: *
Disallow: /private/
Allow: /private/open$
Disallow: /*.pdf$

User-agent: CobRagBot
User-Agent: otherbot
Disallow: /no-bots/
Allow: /no-bots/menu
Crawl-delay: 30

Sitemap: https://shreedental.in/sitemap_index.xml
"""


def test_our_group_replaces_the_star_group() -> None:
    r = parse_robots(ROBOTS, bot="CobRagBot")
    assert r.allowed("/private/x")  # our group says nothing about /private/
    assert not r.allowed("/no-bots/x")
    assert r.allowed("/no-bots/menu") and r.allowed("/no-bots/menu-card")
    assert r.crawl_delay == 10.0  # capped
    assert r.sitemaps == ("https://shreedental.in/sitemap_index.xml",)


def test_star_group_longest_match_and_end_anchor() -> None:
    r = parse_robots(ROBOTS, bot="SomeoneElse")
    assert not r.allowed("/private/x")
    assert r.allowed("/private/open")
    assert not r.allowed("/private/open/more")
    assert not r.allowed("/menu.pdf")
    assert r.allowed("/menu.pdf?download=1")  # "$" anchors the whole path+query
    assert r.allowed("/robots.txt")


def test_group_name_is_case_insensitive_and_groups_merge() -> None:
    text = "User-agent: cobragbot\nDisallow: /a\n\nUser-agent: COBRAGBOT\nDisallow: /b\n"
    r = parse_robots(text, bot="CobRagBot")
    assert not r.allowed("/a") and not r.allowed("/b") and r.allowed("/c")


def test_allow_wins_a_tie_and_empty_disallow_allows_all() -> None:
    assert parse_robots("User-agent: *\nDisallow: /x\nAllow: /x\n", bot="CobRagBot").allowed("/x")
    assert parse_robots("User-agent: *\nDisallow:\n", bot="CobRagBot").allowed("/anything")


def test_percent_encoding_is_normalised() -> None:
    r = parse_robots("User-agent: *\nDisallow: /a%3cd\n", bot="CobRagBot")
    assert not r.allowed("/a%3Cd")


def test_comments_and_a_bom_are_ignored() -> None:
    r = parse_robots("﻿User-agent: * # everyone\nDisallow: /x # no\n", bot="CobRagBot")
    assert not r.allowed("/x") and r.allowed("/y")


@pytest.mark.parametrize(
    "status,allowed",
    [(404, True), (410, True), (403, True), (500, False), (503, False), (0, False)],
)
def test_status_rules(status: int, allowed: bool) -> None:
    assert robots_for_status(status, None, bot="CobRagBot").allowed("/") is allowed


def test_only_the_first_500_kib_is_read() -> None:
    text = "User-agent: *\n" + "Allow: /ok\n" * 60_000 + "Disallow: /late\n"
    assert robots_for_status(200, text, bot="CobRagBot").allowed("/late")


def test_raw_unicode_and_spaces_in_a_pattern_match_the_encoded_path() -> None:
    from callobuzz_rag.website.urls import canonical

    r = parse_robots("User-agent: *\nDisallow: /दाँत\nDisallow: /a b\n", bot="CobRagBot")
    path = canonical("https://x.in/दाँत/seva")
    assert path is not None and not r.allowed(path.removeprefix("https://x.in"))
    spaced = canonical("https://x.in/a b/c")
    assert spaced is not None and not r.allowed(spaced.removeprefix("https://x.in"))
    assert r.allowed("/other")
