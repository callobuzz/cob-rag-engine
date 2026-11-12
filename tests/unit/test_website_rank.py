import random

import pytest

from callobuzz_rag.website.rank import Candidate, score, select_pages

START = "https://shreedental.in/"


def c(url: str, **kw) -> Candidate:
    return Candidate(url=url, depth=kw.pop("depth", 1), **kw)


CANDS = [
    c("https://shreedental.in/", depth=0),
    c(
        "https://shreedental.in/blog/2019/teeth-myths",
        depth=3,
        in_sitemap=True,
        sitemap_priority=0.3,
    ),
    c("https://shreedental.in/pricing", in_nav=True),
    c("https://shreedental.in/contact-us", in_footer=True),
    c(
        "https://shreedental.in/treatments/root-canal",
        depth=2,
        in_sitemap=True,
        sitemap_priority=0.8,
    ),
    c("https://shreedental.in/tag/news", depth=2),
]


def test_scores_follow_the_pinned_formula() -> None:
    s = {x.url: score(x, start_url=START) for x in CANDS}
    assert s[START] == 1000  # start page, depth 0
    assert s["https://shreedental.in/pricing"] == 300 + 150 - 40
    assert s["https://shreedental.in/contact-us"] == 200 + 150 - 40
    assert s["https://shreedental.in/treatments/root-canal"] == 150 + 80 - 80
    assert s["https://shreedental.in/tag/news"] == -80 - 100
    assert s["https://shreedental.in/blog/2019/teeth-myths"] == 30 - 120 - 100


def test_cap_keeps_important_pages_first() -> None:
    assert [x.url for x in select_pages(CANDS, cap=4, start_url=START)] == [
        "https://shreedental.in/",
        "https://shreedental.in/pricing",
        "https://shreedental.in/contact-us",
        "https://shreedental.in/treatments/root-canal",
    ]


@pytest.mark.parametrize("seed", range(50))
def test_order_is_deterministic_under_shuffle(seed: int) -> None:
    shuffled = CANDS[:]
    random.Random(seed).shuffle(shuffled)
    assert select_pages(shuffled, cap=None, start_url=START) == select_pages(
        CANDS, cap=None, start_url=START
    )


def test_ties_break_by_depth_then_length_then_url() -> None:
    a = c("https://shreedental.in/zz")
    b = c("https://shreedental.in/aa")
    d = c("https://shreedental.in/a/b", depth=2)
    assert [x.url for x in select_pages([d, a, b], cap=None, start_url=START)] == [
        b.url,
        a.url,
        d.url,
    ]


def test_keyword_matches_words_not_substrings() -> None:
    assert score(c("https://shreedental.in/our-services"), start_url=START) == 150 - 40
    assert score(c("https://shreedental.in/servicesxyz"), start_url=START) == -40
    assert (
        score(c("https://shreedental.in/x", title="Fees and timings"), start_url=START) == 150 - 40
    )


def test_archive_like_paths_are_penalised() -> None:
    for path in ("/category/news", "/author/dr-shah", "/page/3", "/2024/05/post", "/2024/post"):
        assert score(c("https://shreedental.in" + path), start_url=START) == -40 - 100, path


def test_no_cap_keeps_everything_and_zero_cap_nothing() -> None:
    assert len(select_pages(CANDS, cap=None, start_url=START)) == len(CANDS)
    assert select_pages(CANDS, cap=0, start_url=START) == []
