import io
import os
from pathlib import Path

import pytest

from callobuzz_rag.core.exceptions import StorageError
from callobuzz_rag.storage.local import LocalFileStorageProvider


@pytest.fixture
def store(tmp_path: Path) -> LocalFileStorageProvider:
    return LocalFileStorageProvider(tmp_path / "files")


async def test_round_trip_and_listing(store: LocalFileStorageProvider) -> None:
    await store.ensure_bucket("rag-documents")
    await store.upload(
        "rag-documents", "corpus/c1/doc/a.pdf", io.BytesIO(b"%PDF-1"), "application/pdf"
    )
    await store.upload("rag-documents", "corpus/c1/doc/b.txt", io.BytesIO(b"hello"))
    await store.upload("rag-documents", "corpus/c2/x.txt", io.BytesIO(b"x"))
    assert await store.download("rag-documents", "corpus/c1/doc/a.pdf") == b"%PDF-1"
    assert await store.exists("rag-documents", "corpus/c1/doc/b.txt")
    listed = await store.list_objects("rag-documents", "corpus/c1/")
    assert [o.key for o in listed] == ["corpus/c1/doc/a.pdf", "corpus/c1/doc/b.txt"]
    assert listed[1].size == 5 and listed[1].last_modified.tzinfo is not None
    assert await store.delete_prefix("rag-documents", "corpus/c1/") == 2
    assert [o.key for o in await store.list_objects("rag-documents")] == ["corpus/c2/x.txt"]


async def test_prefix_is_a_folder_boundary_not_a_string_prefix(store) -> None:
    await store.upload("b", "corpus/c1/a", io.BytesIO(b"1"))
    await store.upload("b", "corpus/c10/a", io.BytesIO(b"2"))
    assert await store.delete_prefix("b", "corpus/c1/") == 1
    assert await store.exists("b", "corpus/c10/a")


async def test_delete_prefix_also_removes_a_crashed_uploads_temp_file(
    store: LocalFileStorageProvider, tmp_path: Path
) -> None:
    await store.ensure_bucket("b")
    await store.upload("b", "corpus/c1/a.txt", io.BytesIO(b"a"))
    leftover = tmp_path / "files" / "b" / "corpus" / "c1" / ".tmp-x"
    leftover.write_bytes(b"partial")
    assert await store.delete_prefix("b", "corpus/c1/") == 1
    assert not (tmp_path / "files" / "b" / "corpus" / "c1").exists()


async def test_delete_prefix_refuses_what_could_match_everything(store) -> None:
    await store.upload("b", "corpus/c1/a", io.BytesIO(b"1"))
    for prefix in ("", "/", "corpus/c1", "corpus"):
        with pytest.raises(ValueError):
            await store.delete_prefix("b", prefix)
    assert await store.exists("b", "corpus/c1/a")


async def test_delete_prefix_removes_emptied_folders_but_keeps_the_bucket(
    store, tmp_path: Path
) -> None:
    await store.upload("b", "corpus/c1/doc/a", io.BytesIO(b"1"))
    await store.delete_prefix("b", "corpus/c1/doc/")
    assert not (tmp_path / "files" / "b" / "corpus").exists()
    assert (tmp_path / "files" / "b").is_dir()


async def test_string_prefix_listing_matches_like_s3(store) -> None:
    await store.upload("b", "corpus/c1/a", io.BytesIO(b"1"))
    await store.upload("b", "corpus/c10/a", io.BytesIO(b"2"))
    assert [o.key for o in await store.list_objects("b", "corpus/c1")] == [
        "corpus/c1/a",
        "corpus/c10/a",
    ]


@pytest.mark.parametrize(
    "key",
    [
        "../escape",
        "corpus/../../escape",
        "/etc/passwd",
        "C:/Windows/x",
        "C:\\x",
        "corpus\\..\\x",
        "corpus//x",
        "",
        "corpus/x\x00.pdf",
        "./x",
        "corpus/./x",
    ],
)
async def test_keys_cannot_leave_the_bucket(store, tmp_path: Path, key: str) -> None:
    with pytest.raises(StorageError, match="invalid object key"):
        await store.upload("b", key, io.BytesIO(b"x"))
    with pytest.raises(StorageError, match="invalid object key"):
        await store.download("b", key)
    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize("bucket", ["..", "a/b", "", "A\\b", "."])
async def test_bucket_names_are_single_safe_segments(store, bucket: str) -> None:
    with pytest.raises(StorageError, match="invalid bucket"):
        await store.ensure_bucket(bucket)


async def test_symlink_inside_the_root_cannot_point_outside(store, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    await store.ensure_bucket("b")
    try:
        os.symlink(outside, tmp_path / "files" / "b" / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")
    with pytest.raises(StorageError, match="invalid object key"):
        await store.upload("b", "link/x", io.BytesIO(b"x"))
    assert not (outside / "x").exists()


async def test_write_is_atomic_and_leaves_no_temp_files(store, tmp_path: Path) -> None:
    await store.upload("b", "k/one", io.BytesIO(b"first"))
    await store.upload("b", "k/one", io.BytesIO(b"second"))
    assert await store.download("b", "k/one") == b"second"
    assert sorted(p.name for p in (tmp_path / "files" / "b" / "k").iterdir()) == ["one"]


async def test_a_failed_write_leaves_no_temp_file(store, tmp_path: Path) -> None:
    class Broken(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            raise OSError("disk went away")

    with pytest.raises(StorageError):
        await store.upload("b", "k/one", Broken())
    assert list((tmp_path / "files" / "b" / "k").iterdir()) == []


async def test_missing_object_is_a_storage_error_and_delete_is_idempotent(store) -> None:
    with pytest.raises(StorageError):
        await store.download("b", "nope")
    await store.delete("b", "nope")  # no error, like S3
    assert not await store.exists("b", "nope")


async def test_presigned_urls_are_refused_with_a_plain_reason(store) -> None:
    with pytest.raises(StorageError, match="presigned URLs are not available"):
        await store.get_presigned_url("b", "k")


async def test_bucket_exists_is_false_when_the_root_is_a_file(tmp_path: Path) -> None:
    blocker = tmp_path / "files"
    blocker.write_text("not a folder")
    store = LocalFileStorageProvider(blocker)
    assert await store.bucket_exists("b") is False
