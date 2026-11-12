import hashlib
import json
from pathlib import Path

import httpx
import pytest

from callobuzz_rag.vectordb.snapshots import SnapshotError, create_snapshots, restore_snapshots

KEY = "qdrant-key-for-tests"


class FakeQdrant:
    def __init__(self, collections=("corpus_a", "corpus_b")):
        self.collections = list(collections)
        self.snapshots: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.uploaded: dict[str, bytes] = {}
        self.fail_on: str | None = None

    def handle(self, req: httpx.Request) -> httpx.Response:
        assert req.headers["api-key"] == KEY
        parts = req.url.path.strip("/").split("/")
        if req.method == "GET" and parts == ["collections"]:
            return httpx.Response(
                200, json={"result": {"collections": [{"name": c} for c in self.collections]}}
            )
        c = parts[1]
        if c == self.fail_on:
            return httpx.Response(500, json={"status": {"error": "disk full"}})
        if req.method == "POST" and parts[2:] == ["snapshots"]:
            name = f"{c}-1.snapshot"
            self.snapshots[name] = f"data of {c}".encode()
            return httpx.Response(200, json={"result": {"name": name, "size": 9}})
        if req.method == "GET" and parts[2] == "snapshots":
            return httpx.Response(200, content=self.snapshots[parts[3]])
        if req.method == "DELETE" and parts[2] == "snapshots":
            self.deleted.append(parts[3])
            return httpx.Response(200, json={"result": True})
        if req.method == "POST" and parts[2:] == ["snapshots", "upload"]:
            assert req.url.params["priority"] == "snapshot"
            assert b'name="snapshot"' in req.content
            self.uploaded[c] = req.content
            return httpx.Response(200, json={"result": True})
        return httpx.Response(404)


def client(fake: FakeQdrant) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="http://qdrant:6333",
        headers={"api-key": KEY},
        transport=httpx.MockTransport(fake.handle),
    )


async def test_create_downloads_every_collection_and_cleans_up(tmp_path: Path) -> None:
    fake = FakeQdrant()
    async with client(fake) as c:
        files = await create_snapshots(c, tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert sorted(manifest) == ["corpus_a", "corpus_b"]
    for entry in manifest.values():
        data = (tmp_path / entry["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"] and entry["size"] == len(data)
    assert sorted(fake.deleted) == ["corpus_a-1.snapshot", "corpus_b-1.snapshot"]
    assert len(files) == 2
    assert not list(tmp_path.glob("*.part"))


async def test_an_empty_qdrant_writes_an_empty_manifest(tmp_path: Path) -> None:
    async with client(FakeQdrant(collections=())) as c:
        assert await create_snapshots(c, tmp_path) == []
    assert json.loads((tmp_path / "manifest.json").read_text()) == {}


async def test_a_failure_names_the_collection_and_leaves_no_manifest(tmp_path: Path) -> None:
    fake = FakeQdrant()
    fake.fail_on = "corpus_b"
    async with client(fake) as c:
        with pytest.raises(SnapshotError, match="corpus_b"):
            await create_snapshots(c, tmp_path)
    # an incomplete backup is never mistaken for a good one
    assert not (tmp_path / "manifest.json").exists()


async def test_an_old_manifest_is_removed_before_a_new_run(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text("{}")
    fake = FakeQdrant()
    fake.fail_on = "corpus_a"
    async with client(fake) as c:
        with pytest.raises(SnapshotError):
            await create_snapshots(c, tmp_path)
    assert not (tmp_path / "manifest.json").exists()


async def test_restore_checks_every_file_before_uploading_any(tmp_path: Path) -> None:
    fake = FakeQdrant()
    async with client(fake) as c:
        await create_snapshots(c, tmp_path)
    (tmp_path / "corpus_b-1.snapshot").write_bytes(b"tampered")
    restored = FakeQdrant()
    async with client(restored) as c:
        with pytest.raises(SnapshotError, match="corpus_b"):
            await restore_snapshots(c, tmp_path)
    assert restored.uploaded == {}


async def test_restore_uploads_each_snapshot(tmp_path: Path) -> None:
    async with client(FakeQdrant()) as c:
        await create_snapshots(c, tmp_path)
    target = FakeQdrant(collections=())
    async with client(target) as c:
        assert sorted(await restore_snapshots(c, tmp_path)) == ["corpus_a", "corpus_b"]
    assert set(target.uploaded) == {"corpus_a", "corpus_b"}


async def test_a_manifest_cannot_point_outside_the_folder(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text(
        json.dumps({"c": {"file": "../secret", "sha256": "0" * 64, "size": 1}})
    )
    async with client(FakeQdrant(collections=())) as c:
        with pytest.raises(SnapshotError, match="c"):
            await restore_snapshots(c, tmp_path)


def test_cli_exit_codes(tmp_path: Path) -> None:
    from callobuzz_rag.vectordb import snapshots

    assert snapshots.main(["restore", str(tmp_path / "missing")]) == 2
    assert snapshots.main(["frobnicate", str(tmp_path)]) == 2
    (tmp_path / "manifest.json").write_text("not json")
    assert snapshots.main(["restore", str(tmp_path)]) == 2
