from urllib.parse import urlsplit

import pytest

from callobuzz_rag.website.urls import (
    UrlError,
    canonical,
    external_id,
    in_scope,
    is_excluded,
    origin_of,
    site_scope,
)


@pytest.mark.parametrize(
    "origin,url,ok",
    [
        ("https://shreedental.in", "https://shreedental.in/services", True),
        ("https://shreedental.in", "https://www.shreedental.in/contact", True),
        ("https://www.shreedental.in", "https://shreedental.in/", True),
        ("https://shreedental.in", "https://blog.shreedental.in/post", True),
        ("https://shreedental.in", "http://shreedental.in/old-page", True),
        ("https://shreedental.in", "https://shreedental.in.evil.com/", False),
        ("https://shreedental.in", "https://evilshreedental.in/", False),
        ("https://shreedental.in", "https://co.in/", False),
        ("https://shop.shreedental.in", "https://shreedental.in/", False),
        ("https://shreedental.in", "https://shreedental.in:8443/", False),
        ("http://shreedental.in:8080", "http://shreedental.in:8080/a", True),
        ("https://shreedental.in", "ftp://shreedental.in/", False),
        ("https://shreedental.in", "https://user:pw@shreedental.in/", False),
    ],
)
def test_in_scope(origin: str, url: str, ok: bool) -> None:
    parts = urlsplit(origin)
    assert in_scope(url, site_scope(origin), port=parts.port) is ok


@pytest.mark.parametrize(
    "bad",
    [
        "*",
        "http://127.0.0.1",
        "http://localhost:3000",
        "https://app",
        "not a url",
        "https://[::1]",
        "https://123-atari-embeds.googleusercontent.com",
    ],
)
def test_site_scope_refuses_non_sites(bad: str) -> None:
    with pytest.raises(UrlError):
        site_scope(bad)


def test_site_scope_drops_www_and_case() -> None:
    assert site_scope("https://www.ShreeDental.in") == "shreedental.in"


@pytest.mark.parametrize(
    "raw,origin",
    [
        ("shreedental.in", "https://shreedental.in"),
        ("  https://www.shreedental.in/pricing?x=1 ", "https://www.shreedental.in"),
        ("http://shreedental.in:8080/", "http://shreedental.in:8080"),
        ("HTTPS://ShreeDental.in:443", "https://shreedental.in"),
    ],
)
def test_origin_of(raw: str, origin: str) -> None:
    assert origin_of(raw) == origin


@pytest.mark.parametrize("bad", ["", "not a url", "localhost", "10.0.0.1", "ftp://a.in", "a.in:xx"])
def test_origin_of_refuses(bad: str) -> None:
    with pytest.raises(UrlError):
        origin_of(bad)


@pytest.mark.parametrize(
    "raw,canon",
    [
        (
            "HTTPS://ShreeDental.in:443/Services/?utm_source=x#top",
            "https://shreedental.in/Services/",
        ),
        ("https://shreedental.in/index.html", "https://shreedental.in/"),
        ("https://shreedental.in/a%2fb", "https://shreedental.in/a%2Fb"),
        ("https://shreedental.in", "https://shreedental.in/"),
        ("mailto:hi@shreedental.in", None),
        ("tel:+919822012345", None),
        ("javascript:void(0)", None),
        ("https://u:p@shreedental.in/", None),
    ],
)
def test_canonical(raw: str, canon: str | None) -> None:
    assert canonical(raw) == canon


def test_canonical_resolves_relative_links() -> None:
    assert (
        canonical("../pricing#x", base="https://shreedental.in/services/root-canal")
        == "https://shreedental.in/pricing"
    )


@pytest.mark.parametrize(
    "url,hit",
    [
        ("https://shreedental.in/cart", "/cart"),
        ("https://shreedental.in/my-account/orders", "/my-account"),
        ("https://shreedental.in/wp-admin/x", "/wp-admin"),
        ("https://shreedental.in/search", "/search"),
        ("https://shreedental.in/shop/Checkout/step", "/checkout"),
        ("https://shreedental.in/cartoons", None),
        ("https://shreedental.in/blog/2019/x", "/blog/*"),
        ("https://shreedental.in/blog", "/blog/*"),
        ("https://shreedental.in/careers/jobs", "/careers"),
        ("https://shreedental.in/services", None),
    ],
)
def test_excludes(url: str, hit: str | None) -> None:
    assert is_excluded(url, ["/blog/*", "/careers"]) == hit


def test_external_id_is_bounded() -> None:
    assert external_id("https://shreedental.in/pricing") == "web:https://shreedental.in/pricing"
    long = "https://shreedental.in/" + "a" * 600
    assert external_id(long).startswith("web:sha256:") and len(external_id(long)) <= 500


def test_a_unicode_host_matches_a_punycode_scope() -> None:
    scope = site_scope("https://दंत.भारत")
    assert scope == "xn--i1b2ee.xn--h2brj9c"
    assert in_scope("https://दंत.भारत/x", scope)
    assert in_scope("https://www.दंत.भारत/x", scope)
    assert canonical("https://दंत.भारत/x") == "https://xn--i1b2ee.xn--h2brj9c/x"
    assert not in_scope("https://और.भारत/x", scope)
