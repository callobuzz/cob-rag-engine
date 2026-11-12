"""CSV export of leads, safe to open in a spreadsheet."""

import csv
import io
import json
import re
from collections.abc import Iterable, Iterator
from typing import Any

CSV_COLUMNS = [
    "created_at",
    "reference",  # TV2 reference
    "knowledge_base",
    "name",
    "phone",
    "email",
    "custom",
    "status",
    "qualification",
    "trigger",
    "tags",
    "language",
    "page_url",
    "utm_source",
    "utm_campaign",
    "assignee_user_id",
    "notes",
]
_PHONE = re.compile(r"^\+\d{8,15}$")
_RISKY = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> str:
    """A cell a spreadsheet will not run as a formula: text that starts with = + - @ tab or
    CR gets a leading apostrophe. A plain phone number (+ and digits) is left as it is."""
    if value is None:
        return ""
    text = str(value)
    if text.startswith(_RISKY) and not _PHONE.match(text):
        return "'" + text
    return text


def lead_row(
    lead: Any,
    fields: dict[str, Any],
    kb_name: str,
    notes: str | None,
    ref_code: str | None = None,  # TV2 reference
) -> list[str]:
    utm = lead.utm or {}
    custom = fields.get("custom")
    values = [
        lead.created_at.isoformat(),
        ref_code,  # TV2 reference
        kb_name,
        fields.get("name"),
        fields.get("phone"),
        fields.get("email"),
        json.dumps(custom, ensure_ascii=False) if custom else "",
        lead.status,
        lead.qualification,
        lead.trigger,
        ";".join(lead.tags or []),
        lead.language,
        lead.page_url,
        utm.get("utm_source"),
        utm.get("utm_campaign"),
        str(lead.assignee_user_id) if lead.assignee_user_id else "",
        notes,
    ]
    return [safe_cell(v) for v in values]


def iter_csv(rows: Iterable[list[str]]) -> Iterator[str]:
    """The header (behind a byte-order mark, so Excel reads UTF-8) then one chunk per row."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    yield "﻿" + buffer.getvalue()
    for row in rows:
        buffer.seek(0)
        buffer.truncate(0)
        writer.writerow(row)
        yield buffer.getvalue()
