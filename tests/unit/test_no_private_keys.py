"""No private key may ever be committed (spec: "a test that fails if a PEM is tracked")."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# Built from parts so this file does not match its own search.
MARKERS = (
    "-----BEGIN " + "PRIVATE KEY-----",
    "-----BEGIN " + "OPENSSH PRIVATE KEY-----",
    "-----BEGIN " + "EC PRIVATE KEY-----",
    "-----BEGIN " + "RSA PRIVATE KEY-----",
)
KEY_SUFFIXES = (".pem", ".key", ".p8", ".pk8")


def _tracked() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [ROOT / p for p in out.stdout.decode().split("\0") if p]


def test_no_key_files_are_tracked() -> None:
    bad = [p.name for p in _tracked() if p.suffix.lower() in KEY_SUFFIXES]
    assert bad == [], f"private key files are tracked: {bad}"


def test_no_tracked_file_contains_a_private_key() -> None:
    bad = []
    for path in _tracked():
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore")
        if any(m in text for m in MARKERS):
            bad.append(path.name)
    assert bad == []


def test_gitignore_keeps_pem_files_out() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "*.pem" in ignore
