"""Assistant avatars: validate, re-encode, store, serve (BIZ4, D16).

Nothing a business uploads is ever served as it came in. The bytes are decoded with Pillow
(PNG, JPEG or WebP only; SVG and GIF are refused), centre-cropped to a square and written out
again as a 256x256 PNG, which drops metadata and anything hidden after the image data. The
public route serves only that PNG, by an unguessable id, with headers that stop a browser from
treating it as anything else.
"""

import asyncio
import io
import logging
import re
import uuid
import warnings
from typing import TYPE_CHECKING, Any, Final

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.db.models.public_asset import PublicAsset
from callobuzz_rag.storage.factory import object_storage

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.db.models.corpus import Corpus

logger = logging.getLogger(__name__)

AVATAR_SIZE: Final = 256
MIN_SIDE: Final = 64
MAX_PIXELS: Final = 25_000_000  # declared size, checked before any pixel is decoded
ALLOWED_FORMATS: Final = frozenset({"PNG", "JPEG", "MPO", "WEBP"})  # MPO: a phone camera's JPEG
KEEP_AVATARS: Final = 5  # per knowledge base: the newest few, plus any the assistant still shows
PUBLIC_PREFIX: Final = "/api/v1/public/avatars/"
NAME_PATTERN: Final = re.compile(
    r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.png$"
)
SERVE_HEADERS: Final = {
    "Cache-Control": "public, max-age=31536000, immutable",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Cross-Origin-Resource-Policy": "cross-origin",
}

INVALID: Final = (
    "That file is not a usable picture. Use a PNG, JPG or WebP image at least 64 pixels wide "
    "and tall."
)


def avatar_path(asset_id: uuid.UUID) -> str:
    return f"{PUBLIC_PREFIX}{asset_id}.png"


def is_avatar_path(value: str) -> bool:
    """Exactly `/api/v1/public/avatars/<uuid>.png`: nothing before, after or in between."""
    if not value.startswith(PUBLIC_PREFIX):
        return False
    return NAME_PATTERN.match(value[len(PUBLIC_PREFIX) :]) is not None


def storage_key(corpus_id: uuid.UUID, asset_id: uuid.UUID) -> str:
    # Under `corpus/{id}/`, the prefix corpus deletion already clears.
    return f"corpus/{corpus_id}/avatars/{asset_id}.png"


async def read_limited(upload: Any, limit: int) -> bytes:
    """At most `limit + 1` bytes: one over means "too large" without reading the rest."""
    data = await upload.read(limit + 1)
    return bytes(data)


def reencode(data: bytes) -> bytes:
    """The 256x256 PNG for `data`, or `APIError(422, avatar_invalid)`.

    Raises:
        APIError: not PNG/JPEG/WebP, unreadable, declared far too large, or under 64 px.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                if probe.format not in ALLOWED_FORMATS:
                    raise ValueError("format")
                width, height = probe.size
                if width * height > MAX_PIXELS:
                    raise ValueError("too many pixels")
                if min(width, height) < MIN_SIDE:
                    raise ValueError("too small")
                probe.load()  # a truncated or doctored file fails here, not later
                picture = ImageOps.exif_transpose(probe)
                has_alpha = "A" in picture.getbands() or "transparency" in picture.info
                picture = picture.convert("RGBA" if has_alpha else "RGB")
        square = ImageOps.fit(picture, (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        square.save(out, "PNG", optimize=True)  # no exif, no text chunks: nothing is copied over
        return out.getvalue()
    except (UnidentifiedImageError, ValueError, OSError, Image.DecompressionBombError, Warning):
        raise APIError(422, INVALID, "avatar_invalid") from None


async def save_avatar(
    session: "AsyncSession",
    settings: "Settings",
    corpus: "Corpus",
    data: bytes,
    *,
    created_by: str,
) -> PublicAsset:
    """Re-encode and store `data` for `corpus`; the caller audits and commits.

    The row is flushed BEFORE the file, so a corpus deleted meanwhile fails the flush instead
    of leaving a file nobody owns (same order as an upload to `/ingest`).
    """
    import hashlib

    png = await asyncio.to_thread(reencode, data)
    asset = PublicAsset(
        id=uuid.uuid4(),
        tenant_id=corpus.tenant_id,
        corpus_id=corpus.id,
        kind="avatar",
        content_type="image/png",
        bytes=len(png),
        sha256=hashlib.sha256(png).hexdigest(),
        storage_key="",
        created_by=created_by[:320],
    )
    asset.storage_key = storage_key(corpus.id, asset.id)
    session.add(asset)
    await session.flush()
    try:
        await object_storage(settings).upload(
            settings.s3_bucket, asset.storage_key, io.BytesIO(png), content_type="image/png"
        )
    except Exception:
        await session.rollback()
        logger.error("Upload of %s to object storage failed", asset.storage_key, exc_info=True)
        raise HTTPException(
            status_code=503, detail="Object storage is unavailable; nothing was stored"
        ) from None
    return asset


async def load_avatar(session: "AsyncSession", settings: "Settings", name: str) -> bytes | None:
    """The stored PNG for `<uuid>.png`, or None when the name is malformed or unknown."""
    match = NAME_PATTERN.match(name)
    if match is None:
        return None
    asset = await session.get(PublicAsset, uuid.UUID(match.group(1)))
    if asset is None or asset.kind != "avatar":
        return None
    try:
        return await object_storage(settings).download(settings.s3_bucket, asset.storage_key)
    except Exception:
        logger.warning("avatar %s is in the database but not in storage", asset.id, exc_info=True)
        return None


async def prune_avatars(
    session: "AsyncSession", settings: "Settings", corpus: "Corpus", *, keep: int = KEEP_AVATARS
) -> int:
    """Drop this knowledge base's older avatars beyond the newest `keep`, never one that the
    published or draft assistant still shows. Rows go first and commit; the files follow, so a
    failure leaves an unreferenced file and never a dangling row. Best effort: returns how many
    pictures were removed."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from callobuzz_rag.db.models.assistant_config import (
        STATUS_DRAFT,
        STATUS_PUBLISHED,
        AssistantConfigVersion,
    )

    try:
        assets = list(
            (
                await session.execute(
                    select(PublicAsset).where(
                        PublicAsset.corpus_id == corpus.id, PublicAsset.kind == "avatar"
                    )
                )
            )
            .scalars()
            .all()
        )
        floor = datetime.min.replace(tzinfo=UTC)
        newest_first = sorted(
            enumerate(assets), key=lambda p: (p[1].created_at or floor, p[0]), reverse=True
        )
        stale = [a for _, a in newest_first[keep:]]
        if not stale:
            return 0
        configs = (
            (
                await session.execute(
                    select(AssistantConfigVersion).where(
                        AssistantConfigVersion.corpus_id == corpus.id,
                        AssistantConfigVersion.status.in_([STATUS_PUBLISHED, STATUS_DRAFT]),
                    )
                )
            )
            .scalars()
            .all()
        )
        shown = {
            str(((c.config or {}).get("identity") or {}).get("avatar_url") or "") for c in configs
        }
        doomed = [a for a in stale if avatar_path(a.id) not in shown]
        keys = [a.storage_key for a in doomed]
        for asset in doomed:
            await session.delete(asset)
        await session.commit()
    except Exception:
        logger.warning("could not prune old avatars of %s", corpus.id, exc_info=True)
        return 0
    storage = object_storage(settings)
    for key in keys:
        try:
            await storage.delete(settings.s3_bucket, key)
        except Exception:
            logger.warning("avatar file %s was not removed", key, exc_info=True)
    return len(keys)
