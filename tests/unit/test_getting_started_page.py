import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGE = (ROOT / "docs/getting-started.html").read_text("utf-8")
SECTIONS = [
    "glossary",
    "code",
    "install",
    "first-sign-in",
    "first-business",
    "widget",
    "whatsapp",
    "licence",
    "upgrade",
    "backup",
    "support",
    "faq",
    "troubleshooting",
]


def test_every_spec_topic_has_its_section_in_order() -> None:
    found = re.findall(r'<h2 id="([a-z-]+)"', PAGE)
    assert found == SECTIONS


def test_commands_match_the_scripts() -> None:
    for cmd, script in [
        ("sudo bash cob-rag-engine/deploy/install.sh", "deploy/install.sh"),
        ("sudo bash /opt/cob-rag/current/deploy/upgrade.sh", "deploy/upgrade.sh"),
        ("sudo bash /opt/cob-rag/current/deploy/backup.sh", "deploy/backup.sh"),
        ("sudo bash /opt/cob-rag/current/deploy/restore.sh", "deploy/restore.sh"),
    ]:
        assert cmd in PAGE, cmd
        assert (ROOT / script).exists()
    assert "upgrade.sh --rollback" in PAGE


def test_beginner_rules() -> None:
    text = re.sub(r"<[^>]+>", " ", PAGE)
    for word in ("corpus", "tenant", "pitch.html"):
        assert word.lower() not in text.lower(), word
    assert "chat.sharmafurniture.in" in PAGE
    assert re.search(r"[$₹]\s?\d", text) is None  # no price


def test_every_in_page_link_has_a_target() -> None:
    ids = set(re.findall(r'\bid="([^"]+)"', PAGE))
    for target in re.findall(r'href="#([^"]+)"', PAGE):
        assert target in ids, target
