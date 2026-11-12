"""Every file a Dockerfile COPYs reaches its build context (a Dockerfile COPY needs its `.dockerignore` exception)."""

import subprocess
from pathlib import Path

import pytest

from tests.unit import docker_context as rules

ROOT = Path(__file__).resolve().parents[2]


CONTEXTS = [
    (ROOT, "Dockerfile"),
    (ROOT / "services/dashboard", "Dockerfile"),
    (ROOT / "services/scraper", "Dockerfile"),
]


def _tracked(base: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=base, capture_output=True, check=True)
    return [p for p in out.stdout.decode().split("\0") if p]


@pytest.mark.parametrize(("base", "name"), CONTEXTS)
def test_every_copy_source_reaches_the_build(base: Path, name: str) -> None:
    ignore = base / ".dockerignore"
    lines = ignore.read_text("utf-8").splitlines() if ignore.exists() else []
    tracked = _tracked(base)
    missing = rules.unreachable_copy_sources((base / name).read_text("utf-8"), tracked, lines)
    assert missing == [], f"{base.name}/{name}: COPY sources excluded or absent: {missing}"


def test_matcher_follows_docker_semantics() -> None:
    lines = [
        "docs/*",
        "!docs/cob",
        "!docs/guide.html",
        "*.md",
        "!README.md",
        "services",
        "benchmarks/*",
        "!benchmarks/results",
        "benchmarks/results/*",
        "!benchmarks/results/summary.json",
        "**/node_modules",
    ]
    ex = rules.dockerignore_excluded
    assert ex("docs/requirements.html", lines)
    assert not ex("docs/guide.html", lines)
    assert not ex("docs/cob/pages/licence.md", lines)  # `*.md` has no slash: root only
    assert ex("CLAUDE.md", lines) and not ex("README.md", lines)
    assert ex("services/dashboard/src/main.tsx", lines)  # a parent folder matches
    assert ex("benchmarks/results/beir.json", lines)
    assert not ex("benchmarks/results/summary.json", lines)
    assert ex("clients/js/node_modules/x/index.js", lines)
    assert not ex("src/callobuzz_rag/__init__.py", lines)


def test_copy_sources_skip_other_stages_and_flags() -> None:
    text = (
        "FROM python:3.12-slim AS builder\nCOPY --from=builder /install /usr/local\n"
        "COPY --chown=appuser:appuser src/ ./src/\nCOPY a.txt b.txt ./\n"
    )
    assert rules.copy_sources(text) == ["src/", "a.txt", "b.txt"]
