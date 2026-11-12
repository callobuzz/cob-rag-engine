"""What the public repository must never contain (the old release gates that still matter)."""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _tracked() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [p for p in out.stdout.decode().split("\0") if p]


ENV_FILE = re.compile(r"(^|/)\.env(\.[\w-]+)?$")


def test_no_env_file_is_tracked() -> None:
    bad = [p for p in _tracked() if ENV_FILE.search(p) and not p.endswith(".env.example")]
    assert bad == [], f"env files are tracked: {bad}"


def test_nothing_from_docs_internal_is_tracked() -> None:
    assert [p for p in _tracked() if p.startswith("docs/internal/")] == []


def test_no_scratch_or_probe_file_is_tracked() -> None:
    pattern = re.compile(r"(^|/)(scratch_[^/]*|_x\.py|_sub_tmp\.py|zz-[^/]*|_tmp_[^/]*)$")
    assert [p for p in _tracked() if pattern.search(p)] == []


STORE_LINK = re.compile(
    r"https?://(?:[\w-]+\.)*(?:codecanyon\.net|envato\.com|themeforest\.net)\b", re.IGNORECASE
)
STORE_WORDS = re.compile(
    r"\b(purchase[ -]code|evaluation copy|CodeCanyon|Envato|Extended licen[cs]e|"
    r"Regular licen[cs]e)\b",
    re.IGNORECASE,
)
TEXT = (
    ".py", ".md", ".html", ".js", ".mjs", ".ts", ".tsx", ".sh", ".yml", ".yaml", ".toml", ".json",
    ".txt", ".example", ".jsonl",
)  # fmt: skip
# Past changelog entries may name the store; the notices reproduce third-party texts; applied
# migrations are history; these tests name the words to assert they are absent elsewhere.
WORDS_ALLOWED = {
    "CHANGELOG.md",
    "THIRD_PARTY_NOTICES.txt",
    "tests/unit/test_public_repo.py",
    "tests/unit/test_public_pages.py",
    "tests/unit/test_licence_files.py",
    "tests/unit/test_setup_wizard.py",
    "tests/integration/test_deploy_scripts.py",
    "tests/unit/test_legal_render.py",
    "tests/unit/test_demo_limits.py",
    "services/dashboard/src/test/screens/open-features.test.tsx",  # asserts the words are absent
}


def _text(rel: str) -> str | None:
    path = ROOT / rel
    if not rel.endswith(TEXT) or not path.is_file():
        return None
    return path.read_text("utf-8", errors="replace")


def test_no_store_link_anywhere() -> None:
    hits = [
        rel
        for rel in _tracked()
        if rel != "THIRD_PARTY_NOTICES.txt"
        and (text := _text(rel)) is not None
        and STORE_LINK.search(text)
    ]
    assert hits == [], hits


def test_no_store_words_outside_the_history() -> None:
    hits = [
        rel
        for rel in _tracked()
        if rel not in WORDS_ALLOWED
        and (text := _text(rel)) is not None
        and STORE_WORDS.search(text)
    ]
    assert hits == [], hits


def test_open_source_files_exist() -> None:
    for rel in (
        "LICENSE",
        "COMMERCIAL-LICENSE.md",
        "CLA.md",
        "CODE_OF_CONDUCT.md",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
        ".github/ISSUE_TEMPLATE/config.yml",
        ".github/pull_request_template.md",
    ):
        assert (ROOT / rel).is_file(), rel
