"""Logo, poster and video uploads for the public website (LP1, decisions D14 and D15).

Nothing an operator uploads is served as it came in. Pictures (PNG, JPEG or WebP; never SVG) are
decoded and written out again, which drops metadata and anything hidden after the image data; the
declared size is checked before a pixel is decoded. A video is not transcoded (the image has no
ffmpeg): its first bytes must say MP4 or WebM, and its size is capped. Files live in the engine's
object storage and are served from a local cache directory so a `Range` request (Safari needs
one) never loads the whole object into memory.
"""

import asyncio
import hashlib
import io
import json
import logging
import os
import uuid
import warnings
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.db.models.landing import LandingAsset, LandingSite, LandingVersion
from callobuzz_rag.landing import store
from callobuzz_rag.landing.store import asset_ids_in
from callobuzz_rag.storage.factory import local_root, object_storage

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.core.config import Settings

logger = logging.getLogger(__name__)

MAX_PIXELS: Final = 25_000_000  # declared size, checked before any pixel is decoded
LOGO_MAX_SIDE: Final = 512
POSTER_MAX_SIDE: Final = 1920
ALLOWED_PICTURES: Final = frozenset({"PNG", "JPEG", "MPO", "WEBP"})  # MPO: a phone's JPEG
_MP4_BRANDS: Final = frozenset(
    {b"isom", b"iso2", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"avc1", b"dash", b"M4V "}
)
ORPHAN_GRACE: Final = timedelta(days=7)
MEDIA_HEADERS: Final = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def _unsupported() -> APIError:
    return APIError(
        415,
        "Use an MP4 or WebM video, or a PNG, JPEG or WebP picture.",
        "landing_asset_type",
    )


def too_large(settings: "Settings") -> APIError:
    return APIError(
        413,
        f"That file is too large. Videos can be up to {settings.landing_video_max_mb} MB, "
        f"pictures up to {settings.landing_image_max_mb} MB.",
        "landing_asset_too_large",
    )


# ------------------------------------------------------------------ what a file is


def sniff_video(data: bytes) -> str:
    """`video/mp4` or `video/webm` from the first bytes, or `APIError(415, landing_asset_type)`."""
    head = data[:64]
    if len(head) >= 12 and head[4:8] == b"ftyp" and head[8:12] in _MP4_BRANDS:
        return "video/mp4"
    if head[:4] == b"\x1aE\xdf\xa3" and b"webm" in head:
        return "video/webm"
    raise _unsupported()


def reencode_picture(data: bytes, *, kind: str) -> tuple[bytes, str, tuple[int, int]]:
    """`(bytes, content type, (width, height))` of the clean copy: a logo is a PNG of at most
    512 px, a poster a WebP of at most 1920 px."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                if probe.format not in ALLOWED_PICTURES:
                    raise ValueError("format")
                width, height = probe.size
                if width * height > MAX_PIXELS or min(width, height) < 1:
                    raise ValueError("size")
                probe.load()  # a truncated or doctored file fails here, not later
                picture = ImageOps.exif_transpose(probe)
                has_alpha = "A" in picture.getbands() or "transparency" in picture.info
                limit = LOGO_MAX_SIDE if kind == "logo" else POSTER_MAX_SIDE
                picture = picture.convert("RGBA" if has_alpha or kind == "logo" else "RGB")
        picture.thumbnail((limit, limit), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        if kind == "logo":
            picture.save(out, "PNG", optimize=True)
            content_type = "image/png"
        else:
            picture.convert("RGB").save(out, "WEBP", quality=82, method=4)
            content_type = "image/webp"
        return out.getvalue(), content_type, picture.size
    except (UnidentifiedImageError, ValueError, OSError, Image.DecompressionBombError, Warning):
        raise _unsupported() from None


# ------------------------------------------------------------------ storage and the cache


def cache_dir(settings: "Settings") -> Path:
    if settings.landing_cache_dir.strip():
        return Path(settings.landing_cache_dir.strip())
    return local_root(settings) / "landing-cache"


def _extension(content_type: str) -> str:
    return {
        "image/png": "png",
        "image/webp": "webp",
        "video/mp4": "mp4",
        "video/webm": "webm",
    }[content_type]


def cache_file(settings: "Settings", asset: LandingAsset) -> Path:
    return cache_dir(settings) / f"{asset.sha256}.{_extension(asset.content_type)}"


def object_key(kind: str, asset_id: uuid.UUID, content_type: str) -> str:
    return f"landing/{kind}/{asset_id}.{_extension(content_type)}"


async def put_asset(
    session: "AsyncSession",
    settings: "Settings",
    *,
    kind: str,
    content_type: str,
    data: bytes,
    created_by: str,
    size: tuple[int, int] | None = None,
    key: str | None = None,
    upload_first: bool = False,
) -> LandingAsset:
    """Store `data` and add its row; the caller audits and commits.

    By default the row is flushed before the file, so a failed flush never leaves a file nobody
    owns (a storage failure then rolls the session back). `upload_first` writes the file first
    and touches the session only on success, for work that must not undo anything else (the
    Open Graph images made while publishing).
    """
    asset_id = uuid.uuid4()
    asset = LandingAsset(
        id=asset_id,
        kind=kind,
        content_type=content_type,
        bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        storage_key=key or object_key(kind, asset_id, content_type),
        width=size[0] if size else None,
        height=size[1] if size else None,
        created_by=created_by[:320],
    )

    async def upload() -> None:
        try:
            await object_storage(settings).upload(
                settings.s3_bucket, asset.storage_key, io.BytesIO(data), content_type=content_type
            )
        except Exception:
            logger.error("Upload of %s to object storage failed", asset.storage_key, exc_info=True)
            raise APIError(
                503,
                "File storage is unavailable; nothing was saved.",
                "landing_storage_unavailable",
            ) from None

    if upload_first:
        await upload()
        session.add(asset)
        await session.flush()
    else:
        session.add(asset)
        await session.flush()
        try:
            await upload()
        except APIError:
            await session.rollback()
            raise
    path = cache_file(settings, asset)
    await asyncio.to_thread(_write_atomic, path, data)  # the bytes are in hand: warm the cache
    return asset


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.part")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


async def ensure_cached(settings: "Settings", asset: LandingAsset) -> Path | None:
    """The local file for an asset, fetched from storage once. None when storage no longer has
    it or the bytes do not match the recorded hash."""
    path = cache_file(settings, asset)
    if path.is_file():
        return path
    try:
        data = await object_storage(settings).download(settings.s3_bucket, asset.storage_key)
    except Exception:
        logger.warning("landing asset %s is in the database but not in storage", asset.id)
        return None
    if hashlib.sha256(data).hexdigest() != asset.sha256:
        logger.error("landing asset %s does not match its recorded hash", asset.id)
        return None
    await asyncio.to_thread(_write_atomic, path, data)
    return path


async def delete_asset(session: "AsyncSession", settings: "Settings", asset: LandingAsset) -> None:
    """Remove the row, then (best effort) the stored object and the cached file."""
    path = cache_file(settings, asset)
    key = asset.storage_key
    await session.delete(asset)
    await session.flush()
    try:
        await object_storage(settings).delete(settings.s3_bucket, key)
    except Exception:
        logger.warning("could not delete %s from storage", key, exc_info=True)
    path.unlink(missing_ok=True)


# ------------------------------------------------------------------ who uses a file


async def referenced_ids(session: "AsyncSession") -> tuple[set[str], set[str]]:
    """`(ids the draft uses, ids any published version uses)`."""
    site = (
        await session.execute(select(LandingSite).where(LandingSite.id == store.SITE_ID))
    ).scalar_one_or_none()
    draft = asset_ids_in(site.draft) if site is not None else set()
    published: set[str] = set()
    for version in (await session.execute(select(LandingVersion))).scalars().all():
        published |= asset_ids_in(version.content)
    return draft, published


async def prune_unreferenced(session: "AsyncSession", settings: "Settings") -> int:
    """Delete uploads that neither the draft nor any version uses and that are older than a
    week (a file uploaded a minute ago may be about to be used). Returns how many."""
    draft, published = await referenced_ids(session)
    keep = draft | published
    cutoff = datetime.now(UTC) - ORPHAN_GRACE
    removed = 0
    for asset in await store.list_assets(session):
        if str(asset.id) not in keep and asset.created_at is not None and asset.created_at < cutoff:
            await delete_asset(session, settings, asset)
            removed += 1
    return removed


def referenced_in(content: Any, asset_id: uuid.UUID) -> bool:
    return str(asset_id) in json.dumps(content, default=str)


async def read_limited(upload: Any, limit: int) -> bytes:
    """At most `limit + 1` bytes: one over means "too large" without reading the rest."""
    return bytes(await upload.read(limit + 1))
