"""Text records: content that is already text (FAQ entries, product listings, help
articles, CRM or CMS exports) and is imported without file parsing.

Each record becomes one Document keyed by its caller-supplied id, so a later import
of the same id replaces it, and an unchanged record is skipped.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field

from callobuzz_rag.parsing.models import ElementType, ParsedDocument, ParsedElement

RECORD_MIME_TYPE = "text/x-record"
RECORD_PARSER_VERSION = "records-1.0"
# Ids with this prefix belong to the engine (the assistant's business card). Customer imports
# may not use them, they do not count against a plan's document allowance, and nothing
# reduces their text (no boilerplate stripping).
SYSTEM_RECORD_PREFIX = "system:"


def is_system_record_id(record_id: str | None) -> bool:
    return bool(record_id) and str(record_id).startswith(SYSTEM_RECORD_PREFIX)


@dataclass
class TextRecord:
    id: str
    text: str
    title: str | None = None
    url: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    # BIZ1 markdown: `#`..`######` lines in `text` are headings (website pages), not paragraphs.
    markdown: bool = False
    # /BIZ1 markdown

    def fingerprint(self) -> str:
        payload: dict[str, object] = {
            "title": self.title,
            "text": self.text,
            "url": self.url,
            "metadata": self.metadata,
        }
        if self.markdown:  # BIZ1 markdown: only when set, so every old record keeps its hash
            payload["markdown"] = True
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_parsed(self) -> ParsedDocument:
        elements: list[ParsedElement] = []
        if self.title:
            elements.append(ParsedElement(type=ElementType.HEADING, text=self.title, level=1))
        if self.markdown:  # BIZ1 markdown
            elements.extend(_markdown_elements(self.text))
        else:
            elements.extend(
                ParsedElement(type=ElementType.PARAGRAPH, text=paragraph.strip())
                for paragraph in self.text.split("\n\n")
                if paragraph.strip()
            )
        return ParsedDocument(
            elements=elements,
            source_filename=self.title or self.id,
            mime_type=RECORD_MIME_TYPE,
            parser_name="records",
            parser_version=RECORD_PARSER_VERSION,
        )


# BIZ1 markdown
_HEADING_LINE = re.compile(r"^(#{1,6})\s+(\S.*)$")


def _markdown_elements(text: str) -> list[ParsedElement]:
    """Heading lines become HEADING elements (level = number of `#`); the lines between them
    stay together as paragraphs (a blank line ends one)."""
    elements: list[ParsedElement] = []
    lines: list[str] = []

    def flush() -> None:
        paragraph = "\n".join(lines).strip()
        lines.clear()
        if paragraph:
            elements.append(ParsedElement(type=ElementType.PARAGRAPH, text=paragraph))

    for raw in text.split("\n"):
        line = raw.rstrip()
        match = _HEADING_LINE.match(line)
        if match:
            flush()
            elements.append(
                ParsedElement(
                    type=ElementType.HEADING, text=match.group(2).strip(), level=len(match.group(1))
                )
            )
        elif not line.strip():
            flush()
        else:
            lines.append(line)
    flush()
    return elements


# /BIZ1 markdown


@dataclass
class RecordsResult:
    status: str = "completed"
    error: str | None = None
    failed_at_step: str | None = None
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    chunks_created: int = 0
    vectors_stored: int = 0
    embed_duration_ms: int = 0
    store_duration_ms: int = 0
    total_duration_ms: int = 0
