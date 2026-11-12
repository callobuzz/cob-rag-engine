"""Avatar upload and the public avatar route (BIZ4).

Images are made with Pillow in the test; object storage is an in-memory fake.
"""

import io
import uuid
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import patch

import pytest
from PIL import Image
from pydantic import ValidationError

from callobuzz_rag.conversation.schema import Identity
from callobuzz_rag.db.models.public_asset import PublicAsset
from tests.unit.test_auth_routes import ADMIN, PASSWORD, World, login, req
from tests.unit.test_website_api import make_corpus, with_app

pytestmark = pytest.mark.usefixtures("unlimited_plan")


class MemoryStorage:
    """The three calls avatars use, kept in a dict shared by every instance."""

    objects: ClassVar[dict[str, tuple[bytes, str | None]]] = {}

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    async def upload(
        self, bucket: str, key: str, data: Any, content_type: str | None = None
    ) -> None:
        self.objects[key] = (data.read(), content_type)

    async def download(self, bucket: str, key: str) -> bytes:
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key][0]

    async def delete(self, bucket: str, key: str) -> None:
        self.objects.pop(key, None)


@pytest.fixture(autouse=True)
def storage():
    MemoryStorage.objects = {}
    with patch("callobuzz_rag.assets.avatars.object_storage", MemoryStorage):
        yield MemoryStorage


def image(fmt: str = "JPEG", size: tuple[int, int] = (600, 400), **save: Any) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (30, 120, 200)).save(out, fmt, **save)
    return out.getvalue()


def exif_jpeg() -> bytes:
    pic = Image.new("RGB", (600, 400), (30, 120, 200))
    exif = Image.Exif()
    exif[0x010F] = "SecretCamera"  # Make
    out = io.BytesIO()
    pic.save(out, "JPEG", exif=exif)
    return out.getvalue()


async def upload(app: Any, corpus: Any, data: bytes, name: str = "a.jpg", **kw: Any) -> Any:
    return await req(
        app,
        "POST",
        f"/api/v1/corpora/{corpus.id}/assistant/avatar",
        key=kw.pop("key", ADMIN),
        files={"file": (name, data, "application/octet-stream")},
        **kw,
    )


async def test_a_jpeg_becomes_a_256_square_png_without_metadata(storage) -> None:
    world = World()
    corpus = make_corpus(world)
    with with_app(world) as app:
        res = await upload(app, corpus, exif_jpeg())
    assert res.status_code == 201, res.text
    url = res.json()["avatar_url"]
    asset = world.db.all(PublicAsset)[0]
    assert url == f"/api/v1/public/avatars/{asset.id}.png"
    stored, content_type = storage.objects[asset.storage_key]
    assert asset.storage_key == f"corpus/{corpus.id}/avatars/{asset.id}.png"
    assert content_type == "image/png"
    pic = Image.open(io.BytesIO(stored))
    assert pic.format == "PNG" and pic.size == (256, 256)
    assert not pic.getexif() and b"SecretCamera" not in stored
    assert (asset.kind, asset.content_type, asset.bytes) == ("avatar", "image/png", len(stored))
    assert asset.tenant_id == corpus.tenant_id and asset.corpus_id == corpus.id
    audit = world.audits("assistant.avatar_uploaded")
    assert len(audit) == 1 and audit[0].details["asset_id"] == str(asset.id)


def mpo() -> bytes:
    """A phone camera's multi-picture JPEG: Pillow reports its format as MPO."""
    first = Image.new("RGB", (300, 300), (10, 20, 30))
    second = Image.new("RGB", (300, 300), (30, 20, 10))
    out = io.BytesIO()
    first.save(out, "MPO", save_all=True, append_images=[second])
    return out.getvalue()


@pytest.mark.parametrize("fmt", ["PNG", "WEBP", "MPO"])
async def test_png_webp_and_phone_jpegs_are_accepted(fmt: str) -> None:
    world = World()
    corpus = make_corpus(world)
    data = mpo() if fmt == "MPO" else image(fmt, (300, 300))
    if fmt == "MPO":
        assert Image.open(io.BytesIO(data)).format == "MPO"
    with with_app(world) as app:
        res = await upload(app, corpus, data)
    assert res.status_code == 201, res.text


def svg() -> bytes:
    return b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"></svg>'


PNG_SIGNATURE = bytes([137, 80, 78, 71, 13, 10, 26, 10])


def bomb_png(width: int, height: int) -> bytes:
    import struct
    import zlib

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return PNG_SIGNATURE + chunk(b"IHDR", header) + chunk(b"IEND", b"")


def polyglot() -> bytes:
    return image("PNG", (100, 100))[:8] + b"<html><script>alert(1)</script></html>"


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(svg(), id="svg"),
        pytest.param(image("GIF", (100, 100)), id="gif"),
        pytest.param(polyglot(), id="png-header-then-html"),
        pytest.param(b"", id="empty"),
        pytest.param(image("PNG", (40, 40)), id="too-small"),
        pytest.param(b"not an image at all", id="text"),
    ],
)
async def test_what_is_not_a_proper_picture_is_refused(data: bytes, storage) -> None:
    world = World()
    corpus = make_corpus(world)
    with with_app(world) as app:
        res = await upload(app, corpus, data)
    assert (res.status_code, res.json()["code"]) == (422, "avatar_invalid"), res.text
    assert world.db.all(PublicAsset) == [] and storage.objects == {}


async def test_a_file_over_the_limit_is_refused_after_reading_one_byte_too_many() -> None:
    world = World()
    corpus = make_corpus(world)
    with with_app(world) as app:
        app.state.settings.avatar_max_bytes = 2048
        res = await upload(app, corpus, b"\x89PNG" + b"0" * 5000)
    assert (res.status_code, res.json()["code"]) == (413, "avatar_too_large")


async def test_the_read_is_capped_at_limit_plus_one() -> None:
    from callobuzz_rag.assets.avatars import read_limited

    class Upload:
        def __init__(self) -> None:
            self.asked: list[int] = []

        async def read(self, n: int = -1) -> bytes:
            self.asked.append(n)
            return b"x" * n

    up = Upload()
    data = await read_limited(up, 1000)  # type: ignore[arg-type]
    assert len(data) == 1001 and up.asked == [1001]


async def test_a_decompression_bomb_is_refused() -> None:
    world = World()
    corpus = make_corpus(world)
    bomb = bomb_png(40000, 40000)  # a header declaring 1.6 billion pixels, a few bytes on the wire
    with with_app(world) as app:
        res = await upload(app, corpus, bomb)
    assert (res.status_code, res.json()["code"]) == (422, "avatar_invalid")


async def test_a_member_cannot_upload() -> None:
    world = World()
    corpus = make_corpus(world)
    member = world.user("ravi@shree.example")
    world.member(member, world.shree, "member")
    with with_app(world) as app:
        token = (await login(app, "ravi@shree.example", PASSWORD)).json()["access_token"]
        res = await upload(app, corpus, image(), key=None, token=token)
    assert (res.status_code, res.json()["code"]) == (403, "owner_required")


async def test_an_owner_can_upload_but_not_into_another_workspaces_knowledge_base() -> None:
    world = World()
    mine = make_corpus(world)
    theirs = make_corpus(world, "Pune Clinic", tenant=world.pune)
    owner = world.user("asha@shree.example")
    world.member(owner, world.shree, "owner")
    with with_app(world) as app:
        token = (await login(app, "asha@shree.example", PASSWORD)).json()["access_token"]
        ok = await upload(app, mine, image(), key=None, token=token)
        other = await upload(app, theirs, image(), key=None, token=token)
    assert ok.status_code == 201
    assert other.status_code in (403, 404)


async def test_the_public_avatar_is_the_stored_png_with_safe_headers() -> None:
    world = World()
    corpus = make_corpus(world)
    with with_app(world) as app:
        created = await upload(app, corpus, image())
        res = await req(app, "GET", created.json()["avatar_url"])  # no credential at all
    assert res.status_code == 200
    assert Image.open(io.BytesIO(res.content)).size == (256, 256)
    h = res.headers
    assert h["content-type"] == "image/png"
    assert h["x-content-type-options"] == "nosniff"
    assert h["content-security-policy"] == "default-src 'none'; sandbox"
    assert h["cross-origin-resource-policy"] == "cross-origin"
    assert h["cache-control"] == "public, max-age=31536000, immutable"


@pytest.mark.parametrize("name", [f"{uuid.uuid4()}.png", "not-a-uuid.png", "x.png"])
async def test_an_unknown_or_malformed_avatar_is_not_found(name: str) -> None:
    world = World()
    with with_app(world) as app:
        res = await req(app, "GET", f"/api/v1/public/avatars/{name}")
    assert (res.status_code, res.json()["code"]) == (404, "avatar_not_found")


# ---------------------------------------------------------------- the schema


def test_identity_accepts_the_engines_own_avatar_path() -> None:
    path = f"/api/v1/public/avatars/{uuid.uuid4()}.png"
    assert Identity(avatar_url=path).avatar_url == path
    assert Identity(avatar_url="https://shreedental.in/logo.png").avatar_url


@pytest.mark.parametrize(
    "bad",
    [
        "/api/v1/public/avatars/../x",
        "/api/v1/public/avatars/abc.png",
        f"/api/v1/public/avatars/{uuid.uuid4()}.png?x=1",
        "javascript:alert(1)",
        "http://shreedental.in/logo.png",
        "/etc/passwd",
    ],
)
def test_identity_refuses_other_paths_and_schemes(bad: str) -> None:
    with pytest.raises(ValidationError):
        Identity(avatar_url=bad)


async def test_widget_config_returns_the_absolute_avatar_url() -> None:
    from callobuzz_rag.api.routes.widget import absolute_avatar

    path = f"/api/v1/public/avatars/{uuid.uuid4()}.png"
    assert absolute_avatar(path, "https://rag.example.com") == f"https://rag.example.com{path}"
    assert absolute_avatar("https://x.in/a.png", "https://rag.example.com") == "https://x.in/a.png"
    assert absolute_avatar(None, "https://rag.example.com") is None


# ---------------------------------------------------------------- housekeeping


async def test_only_the_newest_avatars_are_kept(storage) -> None:
    world = World()
    corpus = make_corpus(world)
    with with_app(world) as app:
        urls = [(await upload(app, corpus, image())).json()["avatar_url"] for _ in range(8)]
    kept = {f"/api/v1/public/avatars/{a.id}.png" for a in world.db.all(PublicAsset)}
    assert len(kept) == 5 and kept == set(urls[-5:])
    assert len(storage.objects) == 5  # the files went with the rows


async def test_an_avatar_the_assistant_still_shows_is_never_removed(storage) -> None:
    from callobuzz_rag.db.models.assistant_config import STATUS_PUBLISHED, AssistantConfigVersion

    world = World()
    corpus = make_corpus(world)
    with with_app(world) as app:
        first = (await upload(app, corpus, image())).json()["avatar_url"]
        world.db.add(
            AssistantConfigVersion(
                corpus_id=corpus.id,
                version=1,
                status=STATUS_PUBLISHED,
                config={"identity": {"avatar_url": first}},
                schema_version=1,
                draft_revision=0,
                created_by="t",
            )
        )
        for _ in range(7):
            await upload(app, corpus, image())
    urls = {f"/api/v1/public/avatars/{a.id}.png" for a in world.db.all(PublicAsset)}
    assert first in urls and len(urls) == 6


async def test_the_public_picture_is_rate_limited_per_client() -> None:
    from unittest.mock import AsyncMock

    world = World()
    corpus = make_corpus(world)
    with with_app(world) as app:
        created = await upload(app, corpus, image())
        limiter = SimpleNamespace(_hit=AsyncMock(side_effect=[(True, 0), (False, 30)]))
        app.state.widget_rate_limiter = limiter
        ok = await req(app, "GET", created.json()["avatar_url"])
        limited = await req(app, "GET", created.json()["avatar_url"])
    assert ok.status_code == 200
    assert (limited.status_code, limited.json()["code"]) == (429, "rate_limited")
