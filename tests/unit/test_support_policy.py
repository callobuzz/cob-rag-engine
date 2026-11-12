"""The support and security promises say the same thing everywhere they are made."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUPPORT = (ROOT / "SUPPORT.md").read_text("utf-8")
SECURITY = (ROOT / "SECURITY.md").read_text("utf-8")
WEB = re.sub(r"<[^>]+>", " ", (ROOT / "docs/support.html").read_text("utf-8"))
START = re.sub(r"<[^>]+>", " ", (ROOT / "docs/getting-started.html").read_text("utf-8"))

SUPPORT_FACTS = [
    "GitHub issues",
    "best effort",
    "commercial support",
    "https://callobuzz.com/contact",
]
SECURITY_FACTS = [
    "2 business days",
    "5 business days",
    "14 days",
    "30 days",
    "90 days",
    "ENISA",
    "24 hours",
    "72 hours",
    "CHANGELOG",
]


def _has(text: str, fact: str) -> bool:
    return fact.lower() in re.sub(r"\s+", " ", text).lower()


def test_support_facts_everywhere() -> None:
    for fact in SUPPORT_FACTS:
        for name, text in (
            ("SUPPORT.md", SUPPORT),
            ("support.html", WEB),
            ("getting-started", START),
        ):
            assert _has(text, fact), (name, fact)


def test_security_facts_in_policy_and_page() -> None:
    for fact in SECURITY_FACTS:
        assert _has(SECURITY, fact), fact
        assert _has(WEB, fact), fact


def test_no_github_reporting_and_no_offers() -> None:
    assert "github.com" not in SECURITY.lower()  # a vulnerability is never reported in a public issue
    for text in (SUPPORT, SECURITY, WEB):
        assert re.search(r"[$₹]\s?\d", text) is None
        assert "pitch.html" not in text
