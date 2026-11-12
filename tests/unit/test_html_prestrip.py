"""The HTML pre-pass: page chrome out before the parser sees it."""

from callobuzz_rag.parsing.html_prestrip import prestrip_html

LONG = (
    "The compressor is covered for ten years from the date of purchase in India, and the "
    "motor for five. Claims need the original invoice and the serial number on the unit. "
    "Service visits in Pune, Chennai and Kolkata are free during the warranty period."
)


def _page(body: str) -> bytes:
    return (
        f"<!doctype html><html><head><title>Warranty</title></head><body>{body}</body></html>"
    ).encode()


def test_html_prestrip_removes_nav_header_footer_aside_roles_cookie() -> None:
    page = _page(
        '<header><a href="/">Acme Home</a></header>'
        '<nav><ul><li><a href="/p">Products</a></li></ul></nav>'
        '<div role="navigation">Site menu links</div>'
        '<div role="banner">Banner strip</div>'
        '<div class="cookie-banner"><p>We use cookies to improve your experience.</p></div>'
        '<div id="onetrust-consent-sdk">Consent manager</div>'
        f"<main><h1>Warranty terms</h1><p>{LONG}</p>"
        "<aside><p>Related: read our blog.</p></aside></main>"
        '<div role="complementary">Sidebar widgets</div>'
        "<footer><p>Copyright 2026 Acme Pvt Ltd.</p></footer>"
        '<div role="contentinfo">Legal strip</div>'
    )
    out = prestrip_html(page)
    assert out is not None
    text = out.decode()
    assert "compressor is covered" in text
    assert "Warranty terms" in text
    for gone in (
        "Acme Home",
        "Products",
        "Site menu links",
        "Banner strip",
        "We use cookies",
        "Consent manager",
        "Related: read our blog",
        "Sidebar widgets",
        "Copyright 2026",
        "Legal strip",
    ):
        assert gone not in text, gone


def test_html_prestrip_keeps_body_when_everything_is_chrome() -> None:
    # Every word sits in an element the pass would remove: leave the page as it was.
    page = _page(
        "<nav>Home Products Pricing Contact About Careers Blog Help Login</nav>"
        f"<footer>{LONG[:90]}</footer>"
    )
    assert prestrip_html(page) is None


def test_html_prestrip_returns_none_below_200_characters_of_body_text() -> None:
    page = _page("<nav>Menu</nav><main><p>Short page.</p></main>")
    assert prestrip_html(page) is None


def test_html_prestrip_never_removes_html_body_or_a_container_of_the_article() -> None:
    page = _page(
        f'<div class="cookie-consent-wrapper"><main><h1>Terms</h1><p>{LONG}</p></main></div>'
    ).replace(b"<body>", b'<body class="has-cookie-notice">')
    out = prestrip_html(page)
    # Nothing removable that does not hold the real content: unchanged (None) or intact.
    assert out is None or b"compressor is covered" in out


def test_html_prestrip_keeps_a_header_and_footer_scoped_to_an_article() -> None:
    page = _page(
        "<header>Site banner</header>"
        f"<article><header><h1>Return policy</h1></header><p>{LONG}</p>"
        "<footer><p>Last reviewed in March 2026 by the support team in Pune.</p></footer>"
        "</article>"
    )
    out = prestrip_html(page)
    assert out is not None
    text = out.decode()
    assert "Site banner" not in text
    assert "Return policy" in text
    assert "Last reviewed in March 2026" in text


def test_html_prestrip_output_declares_utf8_and_keeps_non_ascii() -> None:
    body = f"<nav>x</nav><main><h1>वारंटी की शर्तें</h1><p>{LONG} ₹4,999 only.</p></main>"
    out = prestrip_html(_page(body))
    assert out is not None
    text = out.decode("utf-8")
    assert "वारंटी की शर्तें" in text and "₹4,999" in text
    assert "utf-8" in text.lower()


def test_html_prestrip_survives_empty_and_garbage_input() -> None:
    assert prestrip_html(b"") is None
    assert prestrip_html(b"\x00\x01 not html at all") is None
