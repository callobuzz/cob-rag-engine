"""docs/pitch.html repeats the canonical pitch honestly, and the landing page links it (OS4)."""

import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "docs/cob/pages/why-cob-rag-engine.md"
PITCH = ROOT / "docs/pitch.html"
INDEX = ROOT / "docs/index.html"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _plain(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", html.unescape(text).replace("`", "").replace("**", ""))


def _four_lines() -> list[str]:
    body = _read(PAGE)
    script = body.split("## Say it in four lines\n\n", 1)[1].split("\n\n", 1)[0]
    lines = [re.sub(r"^\d\. ", "", x) for x in script.split("\n")]
    assert len(lines) == 4
    return lines


def test_the_rebuttal_in_the_pitch_sheet_matches_the_canonical_page() -> None:
    sheet = _plain(_read(PITCH))
    for line in _four_lines():
        assert _plain(line) in sheet, f"pitch.html is missing: {line[:60]}"


def test_the_pitch_sheet_has_the_attribution_and_the_contact() -> None:
    sheet = _read(PITCH)
    assert "cob-rag-engine by Callobuzz" in sheet
    assert "mailto:callobuzz@gmail.com" in sheet
    assert "callobuzz@gmail.com" in _read(ROOT / "README.md")


def test_the_pitch_sheet_is_linked_from_landing_but_not_the_guide() -> None:
    assert 'href="pitch.html?for=agencies"' in _read(INDEX)
    # the guide is a how-to page; the founder pitch is linked only from the landing page
    assert "pitch.html" not in _read(ROOT / "docs/guide.html")


def test_the_pitch_copy_makes_no_income_promise_and_no_hosted_claim() -> None:
    for path in (INDEX, PITCH):
        text = _plain(_read(path)).lower()
        assert "guarantee" not in text
        assert "hosted cob-rag-engine: you run it" in text


def test_the_sheet_opens_with_the_audience_picked() -> None:
    sheet = _read(PITCH)
    assert 'id="picked"' in sheet and "[?&]for=" in sheet


def test_no_page_offers_a_partner_circle() -> None:
    # L1: the release poll is gone; nothing invites anyone to a circle.
    for path in (INDEX, PITCH):
        assert "partner circle" not in _read(path).lower()
