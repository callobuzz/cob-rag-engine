"""Every file COB's manifest lists is copied into the engine image (Dockerfile) and not dropped
by .dockerignore: without them the sync in a container would find nothing to read."""

import re
from pathlib import Path

from callobuzz_rag.cob.manifest import load_manifest

ROOT = Path(__file__).resolve().parents[2]
_DOUBLE_STAR = "DOUBLESTAR"


def _copied() -> list[str]:
    sources: list[str] = []
    for line in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines():
        if line.startswith("COPY") and "--from" not in line:
            parts = [p for p in line.split()[1:] if not p.startswith("--")]
            sources += parts[:-1]
    return [s.rstrip("/") for s in sources]


def _matches(pattern: str, path: str) -> bool:
    """Docker's pattern: `*` stays inside one path segment, `**` crosses them."""
    marked = pattern.replace("**", _DOUBLE_STAR)
    regex = re.escape(marked).replace(r"\*", "[^/]*").replace(_DOUBLE_STAR, ".*")
    return re.fullmatch(regex, path) is not None


def _ignored(path: str) -> bool:
    """A small model of .dockerignore: patterns in order, `!` re-includes; a pattern that
    matches a parent folder matches everything inside it."""
    ignored = False
    for raw in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        pattern = line.lstrip("!").rstrip("/")
        parts = path.split("/")
        if any(_matches(pattern, "/".join(parts[: i + 1])) for i in range(len(parts))):
            ignored = not negate
    return ignored


def test_manifest_sources_are_in_the_image() -> None:
    copied = _copied()
    assert "pyproject.toml" in copied  # cob/manifest.py finds the project root by it
    manifest = load_manifest(ROOT)
    for source in manifest.sources:
        base = re.split(r"[*?\[]", source.path, maxsplit=1)[0].rstrip("/")
        assert any(base == c or base.startswith(c + "/") for c in copied), (
            f"{source.path} is not copied by the Dockerfile"
        )
        assert not _ignored(base), f"{source.path} is dropped by .dockerignore"


def test_the_manifest_itself_ships() -> None:
    assert not _ignored("docs/cob/manifest.yaml")
    assert not _ignored("docs/cob/screens/widget.md")
    assert not _ignored("docs/cob/generated/api/widget.md")
    assert _ignored("docs/index.html")  # the public site is still not in the image
    assert _ignored("notes.md")  # root Markdown stays out, except the ones re-included
    assert not _ignored("CHANGELOG.md")
