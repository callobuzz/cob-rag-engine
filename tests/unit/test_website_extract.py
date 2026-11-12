from callobuzz_rag.website.extract import extract_page

URL = "https://shreedental.in/services/root-canal"

PAGE = """<!doctype html><html><head><title>Root canal | Shree Dental Clinic, Pune</title>
<meta property="og:title" content="OG title">
<link rel="canonical" href="/treatments/root-canal">
</head><body>
<header><nav><a href="/pricing">Pricing</a> <a href="/contact-us">Contact</a></nav></header>
<div class="cookie-consent">We use cookies. Accept all</div>
<main>
<h1>Root canal treatment</h1>
<p>Our dentists in Pune treat a tooth in one or two sittings. The charge starts from
₹4,500 per tooth and depends on the tooth and the material used for the filling.</p>
<h2>Timings</h2>
<ul><li>Mon-Sat 10:00-19:00</li><li>Sunday closed</li></ul>
<table><tr><th>Service</th><th>Fee</th></tr><tr><td>Cleaning</td><td>₹800</td></tr></table>
<form action="/book"><input name="q"><button>Send</button></form>
<script>var x = "tracking";</script><style>.a{color:red}</style>
<p>Download the <a href="/files/brochure.pdf">brochure</a> or read the
<a href="/blog" rel="nofollow">blog</a>.</p>
</main>
<footer><a href="/privacy">Privacy policy</a></footer>
</body></html>"""


def test_title_headings_lists_and_tables() -> None:
    page = extract_page(PAGE, URL)
    assert page.title == "Root canal | Shree Dental Clinic, Pune"
    md = page.markdown
    assert "# Root canal treatment" in md.splitlines()
    assert "## Timings" in md.splitlines()
    assert "- Mon-Sat 10:00-19:00\n- Sunday closed" in md
    assert "Service | Fee\nCleaning | ₹800" in md
    assert "₹4,500 per tooth" in md


def test_non_content_is_gone() -> None:
    md = extract_page(PAGE, URL).markdown
    for junk in ("tracking", "color:red", "Send", "We use cookies", "Accept all"):
        assert junk not in md


def test_links_carry_their_region_and_nofollow() -> None:
    page = extract_page(PAGE, URL)
    by_url = {link.url: link for link in page.links}
    assert by_url["https://shreedental.in/pricing"].region == "nav"
    assert by_url["https://shreedental.in/contact-us"].region == "nav"
    assert by_url["https://shreedental.in/privacy"].region == "footer"
    assert by_url["https://shreedental.in/blog"].nofollow is True
    assert by_url["https://shreedental.in/pricing"].nofollow is False
    assert page.files == ["https://shreedental.in/files/brochure.pdf"]


def test_canonical_link_is_resolved_against_the_page() -> None:
    assert extract_page(PAGE, URL).canonical_url == "https://shreedental.in/treatments/root-canal"


def test_robots_meta() -> None:
    html = '<html><head><meta name="robots" content="noindex, nofollow"></head><body><p>x</p></body></html>'
    page = extract_page(html, URL)
    assert page.noindex and page.nofollow
    both = extract_page(html.replace("noindex, nofollow", "none"), URL)
    assert both.noindex and both.nofollow
    only = extract_page(html.replace("noindex, nofollow", "noindex"), URL)
    assert only.noindex and not only.nofollow


def test_title_fallbacks() -> None:
    og = '<html><head><meta property="og:title" content="Shree Dental"></head><body><h1>H</h1></body></html>'
    assert extract_page(og, URL).title == "Shree Dental"
    assert (
        extract_page("<html><body><h1>Only heading</h1></body></html>", URL).title == "Only heading"
    )


def test_devanagari_survives_as_bytes_and_text() -> None:
    html = (
        "<html><head><meta charset='utf-8'></head><body><h1>रूट कैनाल</h1><p>₹4,500</p></body></html>"
    )
    for data in (html, html.encode("utf-8")):
        md = extract_page(data, URL).markdown
        assert "रूट कैनाल" in md and "₹4,500" in md


def test_content_hash_ignores_whitespace_only_changes() -> None:
    a = extract_page("<html><body><h1>Fees</h1><p>Root canal   ₹4,500</p></body></html>", URL)
    b = extract_page(
        "<html><body>\n<h1>Fees</h1>\n\n<p>Root canal ₹4,500\n</p>\n</body></html>", URL
    )
    c = extract_page("<html><body><h1>Fees</h1><p>Root canal ₹5,000</p></body></html>", URL)
    assert a.content_hash == b.content_hash != c.content_hash


def test_bad_markup_never_raises() -> None:
    assert extract_page("", URL).markdown == ""
    assert extract_page("<<<not html", URL).words >= 0
    assert extract_page(b"\xff\xfe\x00", URL).words >= 0
