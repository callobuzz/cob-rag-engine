"""LP1 T7: logo, poster and video uploads."""

import io
import struct
import uuid
import zlib
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.landing import LandingAsset, LandingSite
from callobuzz_rag.landing import assets, store
from callobuzz_rag.landing.schema import default_content
from callobuzz_rag.storage.local import LocalFileStorageProvider
from tests.unit.accounts_fakes import FakeDB
from tests.unit.landing_fakes import make_admin_client, make_client, make_env, make_settings

MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 64
WEBM = (
    b"\x1aE\xdf\xa3\x9fB\x86\x81\x01B\xf7\x81\x01B\xf2\x81\x04B\xf3\x81\x08B\x82\x84webm"
    + b"\x00" * 64
)


def _picture(fmt: str, size: tuple[int, int] = (800, 600), mode: str = "RGB") -> bytes:
    out = io.BytesIO()
    Image.new(mode, size, (15, 118, 110) if mode == "RGB" else (15, 118, 110, 120)).save(out, fmt)
    return out.getvalue()


@pytest.mark.parametrize(("data", "expected"), [(MP4, "video/mp4"), (WEBM, "video/webm")])
def test_videos_are_recognised_by_their_bytes(data: bytes, expected: str) -> None:
    assert assets.sniff_video(data) == expected


@pytest.mark.parametrize(
    "data",
    [
        b"<svg onload=alert(1)>",
        b"GIF89a....",
        b"\x00\x00\x00\x18ftypqt  ",
        b"RIFF....AVI ",
        b"\x1aE\xdf\xa3\x9fB\x86\x81\x01matroska",
        b"",
    ],
)
def test_anything_else_is_refused(data: bytes) -> None:
    with pytest.raises(APIError) as err:
        assets.sniff_video(data + b"\x00" * 64)
    assert err.value.status_code == 415 and err.value.code == "landing_asset_type"


def test_pictures_are_reencoded_and_svg_never_accepted() -> None:
    big = _picture("PNG", (2000, 1000))
    out, ctype, (w, h) = assets.reencode_picture(big, kind="logo")
    assert ctype == "image/png" and out.startswith(b"\x89PNG") and max(w, h) <= 512
    out, ctype, (w, h) = assets.reencode_picture(big, kind="poster")
    assert ctype == "image/webp" and out[:4] == b"RIFF" and max(w, h) <= 1920
    with pytest.raises(APIError):
        assets.reencode_picture(b'<svg xmlns="http://www.w3.org/2000/svg"/>', kind="logo")
    for fmt in ("JPEG", "WEBP"):
        assets.reencode_picture(_picture(fmt), kind="poster")
    with pytest.raises(APIError):
        assets.reencode_picture(_picture("GIF"), kind="logo")


def test_a_logo_keeps_its_transparency() -> None:
    out, _, _ = assets.reencode_picture(_picture("PNG", (100, 100), "RGBA"), kind="logo")
    with Image.open(io.BytesIO(out)) as logo:
        assert "A" in logo.getbands()


def test_metadata_does_not_survive_the_reencode() -> None:
    picture = io.BytesIO()
    image = Image.new("RGB", (64, 64), (1, 2, 3))
    exif = Image.Exif()
    exif[0x010E] = "secret description"
    image.save(picture, "JPEG", exif=exif)
    out, _, _ = assets.reencode_picture(picture.getvalue(), kind="poster")
    assert b"secret description" not in out


def _png_header(width: int, height: int) -> bytes:
    """A PNG that only declares a size (what a decompression bomb looks like before decoding)."""

    def chunk(tag: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(tag + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"\x00"))


def test_declared_dimensions_are_checked_before_decoding() -> None:
    with pytest.raises(APIError) as err:  # a decompression bomb is refused from the header alone
        assets.reencode_picture(_png_header(100_000, 100_000), kind="poster")
    assert err.value.code == "landing_asset_type"


# ------------------------------------------------------------------ the API


def _settings(tmp_path: Path, **over: Any) -> Any:
    return make_settings(
        landing_video_max_mb=1,
        landing_image_max_mb=1,
        landing_cache_dir=str(tmp_path / "cache"),
        s3_bucket="rag-documents",
        storage_provider="local",
        storage_local_root=str(tmp_path / "files"),
        **over,
    )


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> LocalFileStorageProvider:
    provider = LocalFileStorageProvider(tmp_path / "files")
    monkeypatch.setattr(assets, "object_storage", lambda _settings: provider)
    return provider


async def _upload(client: Any, kind: str, name: str, data: bytes, ctype: str) -> Any:
    return await client.post(
        "/api/v1/admin/landing/assets", data={"kind": kind}, files={"file": (name, data, ctype)}
    )


async def test_a_logo_is_stored_reencoded_and_listed(tmp_path: Path, storage: Any) -> None:
    db = FakeDB()
    client = make_admin_client(db, _settings(tmp_path))
    res = await _upload(client, "logo", "logo.jpg", _picture("JPEG", (900, 300)), "image/jpeg")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["kind"] == "logo" and body["content_type"] == "image/png"
    assert max(body["width"], body["height"]) <= 512
    assert body["url"] == f"/_landing/media/{body['id']}.png"
    (row,) = db.all(LandingAsset)
    assert row.kind == "logo" and await storage.exists("rag-documents", row.storage_key)
    audit = db.all(AuditLog)
    assert [a.action for a in audit] == ["landing.asset_uploaded"]
    assert audit[0].details == {"kind": "logo", "bytes": row.bytes}
    state = await client.get("/api/v1/admin/landing")
    assert [a["id"] for a in state.json()["assets"]] == [body["id"]]


async def test_a_video_is_checked_by_its_bytes_and_kept_as_it_is(
    tmp_path: Path, storage: Any
) -> None:
    db = FakeDB()
    client = make_admin_client(db, _settings(tmp_path))
    ok = await _upload(client, "video", "demo.mp4", MP4, "video/mp4")
    assert ok.status_code == 200 and ok.json()["content_type"] == "video/mp4"
    (row,) = db.all(LandingAsset)
    assert await storage.download("rag-documents", row.storage_key) == MP4
    bad = await _upload(
        client, "video", "demo.mp4", b"<svg onload=alert(1)>" + b" " * 80, "video/mp4"
    )
    assert bad.status_code == 415 and bad.json()["code"] == "landing_asset_type"
    renamed = await _upload(client, "video", "demo.mp4", _picture("PNG"), "video/mp4")
    assert renamed.status_code == 415  # the name and the declared type prove nothing


async def test_size_limits_follow_settings(tmp_path: Path, storage: Any) -> None:
    db = FakeDB()
    client = make_admin_client(db, _settings(tmp_path))  # 1 MB each
    res = await _upload(client, "video", "big.mp4", MP4 + b"\x00" * (2 * 1024 * 1024), "video/mp4")
    assert res.status_code == 413 and res.json()["code"] == "landing_asset_too_large"
    assert "1 MB" in res.json()["detail"]
    big = await _upload(
        client, "poster", "p.png", b"\x89PNG" + b"\x00" * (2 * 1024 * 1024), "image/png"
    )
    assert big.status_code == 413
    assert db.all(LandingAsset) == []


async def test_a_file_the_draft_or_a_version_uses_cannot_be_deleted(
    tmp_path: Path, storage: Any
) -> None:
    db = FakeDB()
    client = make_admin_client(db, _settings(tmp_path))
    uploaded = (await _upload(client, "logo", "l.png", _picture("PNG"), "image/png")).json()
    asset_id = uploaded["id"]
    content = default_content().model_dump(mode="json")
    content["theme"]["logo_asset_id"] = asset_id
    from callobuzz_rag.landing.schema import LandingContent

    parsed = LandingContent.model_validate(content)
    db.add(
        LandingSite(
            id=1,
            enabled=False,
            draft=parsed.model_dump(mode="json"),
            draft_revision=1,
            draft_theme=store.theme_of(parsed).to_json(),
        )
    )
    res = await client.delete(f"/api/v1/admin/landing/assets/{asset_id}")
    assert res.status_code == 409 and res.json()["code"] == "landing_asset_in_use"
    assert len(db.all(LandingAsset)) == 1


async def test_an_unused_file_is_deleted_from_the_row_the_storage_and_the_cache(
    tmp_path: Path, storage: Any
) -> None:
    db = FakeDB()
    settings = _settings(tmp_path)
    client = make_admin_client(db, settings)
    uploaded = (await _upload(client, "logo", "l.png", _picture("PNG"), "image/png")).json()
    (row,) = db.all(LandingAsset)
    key, cached = row.storage_key, assets.cache_file(settings, row)
    assert cached.is_file()
    res = await client.delete(f"/api/v1/admin/landing/assets/{uploaded['id']}")
    assert res.status_code == 204 and db.all(LandingAsset) == []
    assert not await storage.exists("rag-documents", key) and not cached.exists()
    gone = await client.delete(f"/api/v1/admin/landing/assets/{uuid.uuid4()}")
    assert gone.status_code == 404 and gone.json()["code"] == "landing_asset_not_found"


async def test_publishing_forgets_old_unused_uploads_only(tmp_path: Path, storage: Any) -> None:
    from datetime import UTC, datetime, timedelta

    db = FakeDB()
    settings = _settings(tmp_path)
    client = make_admin_client(db, settings)
    fresh = (await _upload(client, "logo", "l.png", _picture("PNG"), "image/png")).json()
    old = (await _upload(client, "logo", "m.png", _picture("PNG", (90, 90)), "image/png")).json()
    for row in db.all(LandingAsset):
        if str(row.id) == old["id"]:
            row.created_at = datetime.now(UTC) - timedelta(days=30)
    res = await client.post("/api/v1/admin/landing/publish", json={"base_revision": 1})
    assert res.status_code == 200, res.text
    left = {str(a.id) for a in db.all(LandingAsset)}
    assert left == {fresh["id"]}


# ------------------------------------------------------------------ serving


def _published_asset(db: FakeDB, tmp_path: Path, data: bytes, ctype: str, kind: str) -> Any:
    import hashlib

    asset_id = uuid.uuid4()
    ext = {"image/png": "png", "video/mp4": "mp4", "video/webm": "webm"}[ctype]
    return db.add(
        LandingAsset(
            id=asset_id,
            kind=kind,
            content_type=ctype,
            bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            storage_key=f"landing/{kind}/{asset_id}.{ext}",
            created_by="admin key",
        )
    )


async def test_media_is_served_with_range_and_long_cache_only_when_published(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, storage: Any
) -> None:
    from dataclasses import replace

    from tests.unit.landing_fakes import make_site

    data = MP4 + bytes(range(256)) * 8
    db = FakeDB()
    asset = _published_asset(db, tmp_path, data, "video/mp4", "video")
    await storage.upload("rag-documents", asset.storage_key, io.BytesIO(data))
    settings = _settings(tmp_path)
    site = replace(make_site(), asset_ids=frozenset({str(asset.id)}))
    client = make_client(monkeypatch, site=site, db=db, settings=settings)
    name = f"{asset.id}.mp4"
    full = await client.get(f"/_landing/media/{name}")
    assert full.status_code == 200 and full.content == data
    assert full.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert full.headers["content-type"] == "video/mp4"
    assert full.headers["x-content-type-options"] == "nosniff"
    part = await client.get(f"/_landing/media/{name}", headers={"Range": "bytes=0-1023"})
    assert part.status_code == 206
    assert part.headers["content-range"] == f"bytes 0-1023/{len(data)}"
    assert part.content == data[:1024]
    # not named by the published version: 404, even though the file exists
    other = _published_asset(db, tmp_path, data, "video/mp4", "video")
    assert (await client.get(f"/_landing/media/{other.id}.mp4")).status_code == 404


async def test_media_names_are_strict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, storage: Any
) -> None:
    from tests.unit.landing_fakes import make_site

    client = make_client(monkeypatch, site=make_site(), settings=_settings(tmp_path))
    for name in ("../../etc/passwd", "x.mp4", f"{uuid.uuid4()}.exe", f"{uuid.uuid4()}.svg", ".."):
        assert (await client.get(f"/_landing/media/{name}")).status_code == 404
    off = make_client(monkeypatch, site=None, settings=_settings(tmp_path))
    res = await off.get(f"/_landing/media/{uuid.uuid4()}.mp4")
    assert res.status_code == 404 and res.headers["x-cob-landing"] == "off"


async def test_a_draft_upload_is_served_to_the_preview_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, storage: Any
) -> None:
    from callobuzz_rag.landing.preview import make_preview_token

    data = _picture("PNG", (50, 50))
    db = FakeDB()
    asset = _published_asset(db, tmp_path, data, "image/png", "logo")
    await storage.upload("rag-documents", asset.storage_key, io.BytesIO(data))
    settings = _settings(tmp_path)
    client = make_client(monkeypatch, site=None, db=db, settings=settings, env=make_env())
    name = f"{asset.id}.png"
    assert (await client.get(f"/_landing/media/{name}")).status_code == 404  # off, no token
    token = make_preview_token(settings, page="home", lang="en")
    res = await client.get(f"/_landing/media/{name}?t={token}")
    assert res.status_code == 200 and res.headers["cache-control"] == "no-store"
    assert (await client.get(f"/_landing/media/{name}?t=forged.token")).status_code == 404
