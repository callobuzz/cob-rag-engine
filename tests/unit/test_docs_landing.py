"""The public site (docs/) describes what ships today, on one shared light/dark system (OS4)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
PAGES = [
    "index.html",
    "getting-started.html",
    "guide.html",
    "deploy.html",
    "requirements.html",
    "support.html",
    "benchmarks.html",
    "pitch.html",
]
NAV_LINKS = ["index.html", "getting-started.html", "guide.html", "deploy.html", "benchmarks.html"]


def _read(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


def _index() -> str:
    return _read("index.html")


def _section(html: str, section_id: str) -> str:
    start = html.index(f'<section id="{section_id}"')
    return html[start : html.index("</section>", start)]


def _section_any(html: str, section_id: str) -> str:
    start = html.rindex("<section", 0, html.index(f'id="{section_id}"'))
    return html[start : html.index("</section>", start)]


def _main_nav(html: str) -> str:
    start = html.index('<nav aria-label="Main">')
    return html[start : html.index("</nav>", start)]


def test_coming_next_lists_only_what_is_not_built() -> None:
    html = _index()
    nxt = _section(html, "next")
    assert "capture a lead" not in nxt
    for built in ("Razorpay", "WhatsApp", "payment gateway", "Payment gateway"):
        assert built not in nxt, built
    assert "Visitors become leads" in html[: html.index('<section id="next"')]


def test_landing_names_what_it_does_not_do_and_what_it_now_does() -> None:
    html = _index()
    gaps = _section_any(html, "gaps")
    assert "No live takeover inbox" in gaps
    assert "No voice assistant" in gaps
    # built since the first release: no page may say they are missing
    for stale in ("No WhatsApp Business API", "No payments yet", "activate a plan by hand"):
        for name in PAGES:
            assert stale not in _read(name), (name, stale)
    assert "WhatsApp Business number" in html
    assert "GST" in html and "Razorpay" in html


def test_readme_roadmap_and_feature_section() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    coming = readme[readme.index("### Coming next") : readme.index("### Done")]
    assert "capture a lead" not in coming
    assert "Payment gateway" not in coming
    assert "## Assistants, leads and handoff" in readme
    assert "RAG_LEAD_DIGEST_HOUR" in readme
    intro = readme[readme.index("## Assistants, leads and handoff") :][:1800]
    assert "follow in later releases" not in intro
    assert "| Conversation layer | Done |" in readme
    assert "| Payments and a hosted service | Done |" in readme
    assert "collecting payment) is up to you" not in readme


def test_guide_has_the_leads_and_digest_steps_and_a_contents_entry() -> None:
    guide = _read("guide.html")
    assert 'id="leads-digest"' in guide
    assert 'href="#leads-digest"' in guide
    assert "Daily digest" in guide


def test_changelog_and_env_example_know_the_digest_hour() -> None:
    assert "RAG_LEAD_DIGEST_HOUR" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "RAG_LEAD_DIGEST_HOUR=9" in (ROOT / ".env.example").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", PAGES)
def test_every_page_is_on_the_shared_system(name: str) -> None:
    html = _read(name)
    assert '<script src="assets/theme.js"></script>' in html
    assert '<link rel="stylesheet" href="assets/site.css">' in html
    assert "color-scheme: dark" not in html  # light by default; dark is the visitor's choice
    assert '<meta name="theme-color" content="#ffffff">' in html


@pytest.mark.parametrize("name", PAGES)
def test_every_page_has_the_same_main_nav(name: str) -> None:
    nav = _main_nav(_read(name))
    assert re.findall(r'<a href="([^"]+)"', nav.split('class="links"', 1)[1].split("</div>", 1)[0]) == NAV_LINKS
    assert 'data-theme-toggle' in nav and 'aria-pressed="false"' in nav
    assert 'class="gh" href="https://github.com/callobuzz/cob-rag-engine"' in nav
    current = re.findall(r'<a href="([^"]+)" aria-current="page"', nav)
    assert current == ([name] if name in NAV_LINKS else [])
    # the same markup on every page, apart from which link is current
    assert re.sub(r' aria-current="page"', "", nav) == re.sub(r' aria-current="page"', "", _main_nav(_index()))


def test_the_theme_script_defaults_to_light_and_keeps_the_choice() -> None:
    script = (DOCS / "assets/theme.js").read_text(encoding="utf-8")
    assert 'stored() === "dark" ? "dark" : "light"' in script
    assert 'var KEY = "cob.site.theme"' in script
    css = (DOCS / "assets/site.css").read_text(encoding="utf-8")
    assert ':root[data-theme="dark"]{' in css and "color-scheme: light;" in css


def test_compare_is_an_anchor_inside_why() -> None:
    why = _section_any(_index(), "why")
    assert 'id="compare"' in why
    assert "Say it in four lines" in why
    assert 'class="cmp"' in why


def test_the_language_claim_is_a_panel_in_the_measured_section() -> None:
    measured = _section_any(_index(), "measured")
    assert measured.index('class="stats"') < measured.index("<!-- language-claim:start -->")
    assert 'class="lang"' in measured and "Measured in English, Hindi and Hinglish" in measured


def test_the_tour_shows_real_screenshots_in_both_themes() -> None:
    tour = _section_any(_index(), "tour")
    panels = re.findall(r'role="tabpanel" id="tp-([a-z]+)"', tour)
    assert len(panels) == 6
    files = re.findall(r'(?:src|srcset)="(assets/screenshots/[^"]+)"', tour)
    assert len(files) == 6 * 4  # light and dark, desktop and phone
    for f in files:
        assert (DOCS / f).is_file(), f
    for theme in ("light", "dark"):
        assert tour.count(f'class="shot-{theme}"') == 6
