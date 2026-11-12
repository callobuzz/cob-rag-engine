"""Brochures on WhatsApp (TV6): a knowledge-base document sent as a WhatsApp document.

The owner marks up to 20 documents "can be sent on WhatsApp" (PDF, JPG or PNG). When a customer
asks for one ("brochure", "price list", "menu", "rate card", "brochure bhejo", "menu bhejiye",
"प्राइस लिस्ट भेजो"), the best-matching marked documents (at most three) are sent as document
messages. A document that is not marked is never sent, however well it matches, and a customer
who asks when nothing is marked gets the normal answer.

Meta's media ids expire after 30 days: an uploaded file's id is cached on the connection for 25
days, per document version, so a replaced file is uploaded again and the same file is not
uploaded for every customer.
"""

import json
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.channels.whatsapp.graph import GraphError
from callobuzz_rag.conversation.rules import normalize_message
from callobuzz_rag.db.models.channel import ChannelConnection
from callobuzz_rag.db.models.document import Document
from callobuzz_rag.db.models.document_version import DocumentVersion
from callobuzz_rag.text.tokenize import tokens

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_RULES = (
    Path(__file__).resolve().parents[2]
    / "conversation"
    / "data"
    / "rules"
    / "document_request.json"
)
MAX_SHAREABLE = 20
_QUESTION_MARKS = ("?", chr(0xFF1F))  # ASCII and full-width
MAX_SENT = 3
MEDIA_CACHE_DAYS = 25
SENDABLE_TYPES = {
    "application/pdf": (".pdf",),
    "image/jpeg": (".jpg", ".jpeg"),
    "image/png": (".png",),
}
_EXTENSIONS = {ext for exts in SENDABLE_TYPES.values() for ext in exts}
_GENERIC = frozenset({"the", "a", "an", "of", "for", "and", "pdf", "me", "you", "your", "please"})


# ------------------------------------------------------------------ the request


@lru_cache(maxsize=1)
def _rules() -> tuple[re.Pattern[str], re.Pattern[str], re.Pattern[str], int, int]:
    raw = json.loads(_RULES.read_text(encoding="utf-8"))

    def joined(items: list[str]) -> re.Pattern[str]:
        return re.compile(rf"(?:^|\s)(?:{'|'.join(items)})(?:\s|$)")

    return (
        joined(raw["nouns"]),
        joined(raw["requests"]),
        joined(raw["question_words"]),
        int(raw["max_bare_words"]),
        int(raw["max_bare_words_question"]),
    )


def is_document_request(text: str) -> bool:
    """A document word with a request word, or on its own ("menu", "price list please").

    On its own it is only a request when it is not a question: "Is the menu veg?" and "menu me
    paneer hai?" ask about the menu, so they take the normal answer; "menu?" and "price list?"
    (two words at most) still ask for the file."""
    norm = normalize_message(text)
    if not norm:
        return False
    noun, request, question, bare, bare_question = _rules()
    if not noun.search(norm):
        return False
    if request.search(norm):
        return True
    words = len(norm.split())
    if question.search(norm):
        return False
    asked = text.strip().endswith(_QUESTION_MARKS)
    return words <= (bare_question if asked else bare)


# ------------------------------------------------------------------ choosing


def _words(text: str) -> set[str]:
    return {w for w in tokens(text.lower()) if len(w) >= 3 and w not in _GENERIC}


def _name_of(document: Any) -> str:
    stem = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", document.file_name or "")
    meta = document.doc_metadata if isinstance(document.doc_metadata, dict) else {}
    title = meta.get("title") if isinstance(meta.get("title"), str) else ""
    return f"{stem} {title}".strip()


def choose(documents: list[Any], request: str) -> list[Any]:
    """At most three of `documents` for `request`: those whose name or title shares a word with
    it, best first; else all of them when there are no more than three; else none (the normal
    answer then takes the question)."""
    wanted = _words(request)
    scored = sorted(
        ((len(wanted & _words(_name_of(d))), d) for d in documents),
        key=lambda pair: -pair[0],
    )
    hits = [d for score, d in scored if score > 0]
    if hits:
        return hits[:MAX_SENT]
    return list(documents) if len(documents) <= MAX_SENT else []


def sendable(document: Any) -> bool:
    """A PDF, JPG or PNG (by its stored type, or by its file name)."""
    mime = (document.mime_type or "").lower()
    if mime in SENDABLE_TYPES:
        return True
    name = (document.file_name or "").lower()
    return any(name.endswith(ext) for ext in _EXTENSIONS)


def mime_of(document: Any) -> str:
    mime = (document.mime_type or "").lower()
    if mime in SENDABLE_TYPES:
        return mime
    name = (document.file_name or "").lower()
    for known, exts in SENDABLE_TYPES.items():
        if name.endswith(exts):
            return known
    return "application/octet-stream"


# ------------------------------------------------------------------ the shareable list


async def shareable_documents(
    session: "AsyncSession", corpus_id: uuid.UUID, ids: list[Any]
) -> list[Document]:
    """The marked documents that still exist in this knowledge base, in the owner's order. A
    deleted document drops out of the list; nothing else is ever offered."""
    wanted = [uuid.UUID(str(i)) for i in ids][:MAX_SHAREABLE]
    if not wanted:
        return []
    rows = (
        (
            await session.execute(
                select(Document).where(Document.corpus_id == corpus_id, Document.id.in_(wanted))
            )
        )
        .scalars()
        .all()
    )
    by_id = {d.id: d for d in rows}
    return [by_id[i] for i in wanted if i in by_id and sendable(by_id[i])]


async def validate_marked(
    session: "AsyncSession", corpus_id: uuid.UUID, ids: list[uuid.UUID]
) -> list[uuid.UUID]:
    """The ids to store: all in this knowledge base and sendable, no repeats; 422
    `whatsapp_document_invalid` otherwise (another knowledge base's file, or a `.docx`)."""
    unique = list(dict.fromkeys(ids))
    if len(unique) > MAX_SHAREABLE:
        raise APIError(
            422, f"At most {MAX_SHAREABLE} documents can be marked.", "whatsapp_document_invalid"
        )
    if not unique:
        return []
    rows = (
        (
            await session.execute(
                select(Document).where(Document.corpus_id == corpus_id, Document.id.in_(unique))
            )
        )
        .scalars()
        .all()
    )
    found = {d.id: d for d in rows}
    bad = [i for i in unique if i not in found or not sendable(found[i])]
    if bad:
        raise APIError(
            422,
            "Only a PDF, JPG or PNG document of this knowledge base can be sent on WhatsApp.",
            "whatsapp_document_invalid",
        )
    return unique


# ------------------------------------------------------------------ media ids


def cached_media_id(settings: dict[str, Any], key: str, now: datetime) -> str | None:
    entry = (settings.get("media_cache") or {}).get(key)
    if not isinstance(entry, dict):
        return None
    try:
        expires = datetime.fromisoformat(str(entry["expires_at"]))
    except (KeyError, ValueError):
        return None
    media = entry.get("media_id")
    return str(media) if media and expires > now else None


async def media_id_for(
    session_factory: Any,
    *,
    graph: Any,
    storage: Any,
    bucket: str,
    connection: ChannelConnection,
    token: str,
    document: Document,
    now: datetime,
) -> str:
    """Meta's id for the document's current file: the cached one, or the file uploaded now."""
    async with session_factory() as session:
        version = (
            await session.execute(
                select(DocumentVersion.id).where(
                    DocumentVersion.document_id == document.id, DocumentVersion.status == "active"
                )
            )
        ).scalar_one_or_none()
        fresh = await session.get(ChannelConnection, connection.id)
        current = dict(fresh.settings or {}) if fresh is not None else {}
    key = str(version or document.id)
    cached = cached_media_id(current, key, now)
    if cached:
        return cached
    if not document.raw_storage_key:
        raise GraphError("the document has no stored file")
    content = await storage.download(bucket, document.raw_storage_key)
    media_id = await graph.upload_media(
        connection.phone_number_id,
        token=token,
        filename=document.file_name or "document",
        content=content,
        mime=mime_of(document),
    )
    if not media_id:
        raise GraphError("Meta gave no media id")
    async with session_factory() as session:
        # The row is locked from this read to the commit, and the settings are written from what
        # was read under the lock: an owner's edit that committed a moment ago is never undone.
        fresh = (
            await session.execute(
                select(ChannelConnection)
                .where(ChannelConnection.id == connection.id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if fresh is not None:
            settings = dict(fresh.settings or {})
            cache = {
                k: v
                for k, v in (settings.get("media_cache") or {}).items()
                if cached_media_id({"media_cache": {k: v}}, k, now)
            }
            cache[key] = {
                "media_id": media_id,
                "expires_at": (now + timedelta(days=MEDIA_CACHE_DAYS)).isoformat(),
            }
            settings["media_cache"] = cache
            fresh.settings = settings
            await session.commit()
    return str(media_id)


def now_utc() -> datetime:
    return datetime.now(UTC)
