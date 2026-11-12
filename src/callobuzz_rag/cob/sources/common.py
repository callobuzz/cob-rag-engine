"""Helpers the Markdown and HTML loaders share."""

import re
import unicodedata

from callobuzz_rag.cob.manifest import ManifestError, ManifestSource
from callobuzz_rag.cob.types import Audience

_DROP = re.compile(r"[^\w\- ]", re.UNICODE)
_MARKER = re.compile(r"^\s*<!--\s*audience:\s*([a-z]+)\s*-->\s*$")


def slugify(text: str) -> str:
    """GitHub-style heading anchor: lower case, punctuation dropped, spaces to hyphens."""
    text = unicodedata.normalize("NFKC", text).strip().lower()
    text = _DROP.sub("", text).replace(" ", "-")
    return re.sub(r"-{2,}", "-", text).strip("-") or "section"


class Anchors:
    """Hands out anchors, de-duplicating repeats with `-2`, `-3`..."""

    def __init__(self) -> None:
        self._seen: dict[str, int] = {}

    def take(self, wanted: str) -> str:
        count = self._seen.get(wanted, 0) + 1
        self._seen[wanted] = count
        return wanted if count == 1 else f"{wanted}-{count}"


def parse_marker(line: str, where: str) -> Audience | None:
    """The audience of a `<!-- audience: admin -->` line, None if the line is not one."""
    match = _MARKER.match(line)
    if not match:
        return None
    try:
        return Audience(match.group(1))
    except ValueError as exc:
        raise ManifestError(f"{where}: unknown audience '{match.group(1)}'.") from exc


def summary_of(text: str, limit: int = 200) -> str:
    """The first sentence-ish of some Markdown, as plain text."""
    for block in text.split("\n\n"):
        plain = re.sub(r"[`*_#>\[\]]|\(\S+?\)", "", block).strip().replace("\n", " ")
        if len(plain) >= 20 and not plain.startswith(("|", "-", "<!--")):
            plain = re.sub(r"\s+", " ", plain)
            if len(plain) <= limit:
                return plain
            cut = plain[:limit].rsplit(" ", 1)[0]
            return cut.rstrip(".,;:") + "..."
    return ""


def override_for(
    source: ManifestSource, key: str, seen: set[str] | None
) -> tuple[Audience | None, str | None]:
    """The audience/topic override a manifest source declares for a section key."""
    override = source.sections.get(key)
    if override is None:
        return None, None
    if seen is not None:
        seen.add(key)
    return override.audience, override.topic
