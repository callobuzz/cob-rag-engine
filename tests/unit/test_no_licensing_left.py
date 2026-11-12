"""Nothing in the engine, its tests or the dashboard still knows about a licence state (OS2/OS3)."""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# Built from parts so this file does not match itself.
PATTERNS = [
    re.compile(r"callobuzz_rag\." + r"licensing\b"),
    re.compile(r"from callobuzz_rag import " + r"licensing\b"),
    re.compile(r"\bL3" + r" license\b"),
    re.compile(r"\bL3b" + r" server\b"),
    re.compile(r"\blicense_" + r"(switch|notice|locked|feature_locked|limit_reached|state)\b"),
    re.compile(r"\bRAG_" + r"LICENSE_"),
]
ROOTS: tuple[str, ...] = ("src/", "tests/", "scripts/", "deploy/", ".env.example", "clients/")
# OS3 dashboard: OS3 T6 adds "services/dashboard/src/" and "services/dashboard/e2e/" here.
# /OS3 dashboard
ALLOWED = {
    "tests/unit/test_no_licensing_left.py",
    "tests/unit/test_audit_retired_actions.py",  # names the retired actions on purpose
    "tests/integration/test_drop_license_state.py",
    "tests/integration/test_deploy_scripts.py",  # asserts the installer writes no such key
    "tests/unit/test_setup_wizard.py",  # asserts the wizard writes no such key
    "alembic/versions/phase18_001_license_state.py",
    "alembic/versions/phase21_001_drop_license_state.py",
}


def _tracked() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [p for p in out.stdout.decode().split("\0") if p and p.startswith(ROOTS)]


def test_no_file_mentions_the_licence_system() -> None:
    hits: list[str] = []
    for rel in _tracked():
        if rel in ALLOWED or not rel.endswith(
            (".py", ".ts", ".tsx", ".js", ".mjs", ".sh", ".example", ".json")
        ):
            continue
        path = ROOT / rel
        if not path.is_file():  # removed in the working tree, removal staged
            continue
        text = path.read_text("utf-8", errors="replace")
        hits += [f"{rel}: {p.pattern}" for p in PATTERNS if p.search(text)]
    assert hits == [], "\n".join(hits)


def test_the_licensing_package_is_gone() -> None:
    assert not any((ROOT / "src/callobuzz_rag/licensing").rglob("*.py"))
    assert not list((ROOT / "tests/unit").glob("test_license_*.py"))
