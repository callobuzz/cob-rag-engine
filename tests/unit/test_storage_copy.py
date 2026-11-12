"""`python -m callobuzz_rag.storage.copy`: move an existing bucket into the local folder (or back)."""

from datetime import UTC, datetime
from typing import BinaryIO

import pytest

from callobuzz_rag.storage.base import ObjectStorageProvider, StoredObject
from callobuzz_rag.storage.copy import CopyResult, copy_bucket, main


class MemoryStorage(ObjectStorageProvider):
    def __init__(self, objects: dict[str, bytes] | None = None, *, fail_on: str = "") -> None:
        self.objects = dict(objects or {})
        self.fail_on = fail_on
        self.deleted: list[str] = []

    async def upload(
        self, bucket: str, key: str, data: BinaryIO, content_type: str | None = None
    ) -> None:
        if self.fail_on and key == self.fail_on:
            raise OSError("disk full")
        self.objects[key] = data.read()

    async def download(self, bucket: str, key: str) -> bytes:
        return self.objects[key]

    async def delete(self, bucket: str, key: str) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)

    async def delete_prefix(self, bucket: str, prefix: str) -> int:
        raise AssertionError("the copy tool never deletes")

    async def list_objects(self, bucket: str, prefix: str = "") -> list[StoredObject]:
        now = datetime.now(UTC)
        return [
            StoredObject(key=k, size=len(v), last_modified=now)
            for k, v in sorted(self.objects.items())
            if k.startswith(prefix)
        ]

    async def exists(self, bucket: str, key: str) -> bool:
        return key in self.objects

    async def get_presigned_url(self, bucket: str, key: str, expires_in: int = 3600) -> str:
        raise AssertionError("not used")

    async def ensure_bucket(self, bucket: str) -> None:
        return None


BINARY = bytes(range(256)) * 3


async def test_dry_run_reports_and_writes_nothing() -> None:
    src = MemoryStorage({"a": b"1", "b/c": BINARY, "same": b"xx"})
    dst = MemoryStorage({"same": b"yy"})
    result = await copy_bucket(src, dst, "rag", apply=False)
    assert result == CopyResult(
        to_copy=2, bytes=1 + len(BINARY), skipped_same=1, copied=0, failed=0
    )
    assert set(dst.objects) == {"same"}


async def test_apply_copies_missing_and_size_different_and_is_idempotent() -> None:
    src = MemoryStorage({"a": b"1", "b/c": BINARY, "same": b"xx", "grown": b"new-longer"})
    dst = MemoryStorage({"same": b"yy", "grown": b"old", "only-in-dst": b"keep"})
    first = await copy_bucket(src, dst, "rag", apply=True)
    assert (first.copied, first.skipped_same, first.failed) == (3, 1, 0)
    assert dst.objects["b/c"] == BINARY  # byte-identical
    assert dst.objects["grown"] == b"new-longer"
    assert dst.objects["same"] == b"yy"  # equal size: left alone
    assert dst.objects["only-in-dst"] == b"keep"  # never deleted
    assert dst.deleted == [] and src.deleted == []
    second = await copy_bucket(src, dst, "rag", apply=True)
    assert (second.copied, second.to_copy) == (0, 0)


async def test_one_failing_object_does_not_stop_the_rest() -> None:
    src = MemoryStorage({"a": b"1", "b": b"2", "c": b"3"})
    dst = MemoryStorage(fail_on="b")
    result = await copy_bucket(src, dst, "rag", apply=True)
    assert (result.copied, result.failed) == (2, 1)
    assert set(dst.objects) == {"a", "c"}


def test_main_exits_one_naming_the_count_of_failures(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    src = MemoryStorage({"a": b"1", "b": b"2"})
    dst = MemoryStorage(fail_on="b")
    monkeypatch.setattr(
        "callobuzz_rag.storage.copy._build", lambda kind, settings: {"s3": src, "local": dst}[kind]
    )
    code = main(["--from", "s3", "--to", "local", "--bucket", "rag", "--apply"])
    out = capsys.readouterr().out
    assert code == 1
    assert "1 object(s) failed" in out


def test_main_dry_run_says_so_and_prints_no_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    src = MemoryStorage({"a": b"1"})
    dst = MemoryStorage()
    monkeypatch.setenv("RAG_S3_SECRET_KEY", "super-secret-value")
    monkeypatch.setattr(
        "callobuzz_rag.storage.copy._build", lambda kind, settings: {"s3": src, "local": dst}[kind]
    )
    code = main(["--from", "s3", "--to", "local", "--bucket", "rag"])
    out = capsys.readouterr().out
    assert code == 0
    assert "dry run" in out.lower() and "--apply" in out
    assert "super-secret-value" not in out
    assert dst.objects == {}


def test_main_refuses_the_same_side_twice() -> None:
    with pytest.raises(SystemExit):
        main(["--from", "local", "--to", "local"])
