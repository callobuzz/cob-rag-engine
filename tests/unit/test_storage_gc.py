"""`python -m callobuzz_rag.storage.gc`: find (and on --apply delete) stored files whose
corpus or workspace no longer exists. Fake storage; the grouping and safety rules are
what is under test."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from callobuzz_rag.storage.base import StoredObject
from callobuzz_rag.storage.gc import collect_garbage, format_report

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=3)


class FakeStorage:
    def __init__(self, objects: list[StoredObject]) -> None:
        self.objects = list(objects)
        self.deleted: list[str] = []
        self.fail: set[str] = set()

    async def list_objects(self, bucket: str, prefix: str = "") -> list[StoredObject]:
        return [o for o in self.objects if o.key.startswith(prefix)]

    async def delete_prefix(self, bucket: str, prefix: str) -> int:
        if prefix in self.fail:
            raise ConnectionError("s3 down")
        self.deleted.append(prefix)
        gone = [o for o in self.objects if o.key.startswith(prefix)]
        self.objects = [o for o in self.objects if not o.key.startswith(prefix)]
        return len(gone)


def obj(key: str, size: int = 10, modified: datetime = OLD) -> StoredObject:
    return StoredObject(key=key, size=size, last_modified=modified)


LIVE_CORPUS, DEAD_CORPUS = uuid.uuid4(), uuid.uuid4()
LIVE_WS, DEAD_WS = uuid.uuid4(), uuid.uuid4()


def _world() -> FakeStorage:
    return FakeStorage(
        [
            obj(f"corpus/{LIVE_CORPUS}/raw/a"),
            obj(f"corpus/{DEAD_CORPUS}/raw/a", 100),
            obj(f"corpus/{DEAD_CORPUS}/raw/b", 50),
            obj(f"portal/{DEAD_CORPUS}/{uuid.uuid4()}/c/file.txt", 7),
            obj(f"portal/{LIVE_CORPUS}/{LIVE_WS}/c/file.txt"),
            obj(f"portal/{LIVE_CORPUS}/{DEAD_WS}/c/file.txt", 3),
            obj("exports/readme.txt"),  # not a layout the engine writes: never touched
            obj("corpus/not-a-uuid/raw/x"),
        ]
    )


async def _gc(storage: FakeStorage, **kw) -> object:
    return await collect_garbage(
        storage,
        "rag",
        corpora={LIVE_CORPUS},
        workspaces={LIVE_WS},
        now=NOW,
        **kw,
    )


async def test_tax_document_pdfs_are_never_collected() -> None:  # P2 invoices
    key = f"billing/tax-documents/26-27/{uuid.uuid4()}.pdf"
    storage = FakeStorage([obj(key)])
    report = await _gc(storage, apply=True)
    assert report.orphans == [] and report.unrecognised == 1
    assert storage.deleted == [] and [o.key for o in storage.objects] == [key]


async def test_dry_run_lists_orphans_and_deletes_nothing() -> None:
    storage = _world()
    report = await _gc(storage)

    got = {(o.prefix, o.objects, o.bytes) for o in report.orphans}
    assert got == {
        (f"corpus/{DEAD_CORPUS}/", 2, 150),
        (f"portal/{DEAD_CORPUS}/", 1, 7),
        (f"portal/{LIVE_CORPUS}/{DEAD_WS}/", 1, 3),
    }
    assert storage.deleted == []
    assert report.unrecognised == 2
    assert report.applied is False


async def test_apply_deletes_exactly_the_orphans() -> None:
    storage = _world()
    report = await _gc(storage, apply=True)

    assert sorted(storage.deleted) == sorted(o.prefix for o in report.orphans)
    remaining = {o.key for o in storage.objects}
    assert remaining == {
        f"corpus/{LIVE_CORPUS}/raw/a",
        f"portal/{LIVE_CORPUS}/{LIVE_WS}/c/file.txt",
        "exports/readme.txt",
        "corpus/not-a-uuid/raw/x",
    }
    assert all(o.deleted == o.objects for o in report.orphans)


async def test_recent_objects_are_left_for_a_later_run() -> None:
    """A file stored moments ago may belong to a row not committed yet."""
    storage = FakeStorage(
        [
            obj(f"corpus/{DEAD_CORPUS}/raw/old"),
            obj(f"corpus/{DEAD_CORPUS}/raw/new", modified=NOW - timedelta(minutes=5)),
        ]
    )
    report = await _gc(storage, apply=True, min_age=timedelta(hours=1))
    assert report.orphans == []  # the prefix holds a young file: skip all of it this time
    assert report.too_recent == 1
    assert storage.deleted == []


async def test_one_failed_prefix_does_not_stop_the_others() -> None:
    storage = _world()
    storage.fail = {f"corpus/{DEAD_CORPUS}/"}
    report = await _gc(storage, apply=True)
    failed = [o for o in report.orphans if o.error]
    assert [o.prefix for o in failed] == [f"corpus/{DEAD_CORPUS}/"]
    assert len(storage.deleted) == 2
    assert report.failed == 1


async def test_report_is_readable() -> None:
    report = await _gc(_world())
    text = format_report(report)
    assert f"corpus/{DEAD_CORPUS}/" in text
    assert "corpus deleted" in text and "workspace deleted" in text
    assert "3 prefixes, 4 objects, 160 B" in text
    assert "--apply" in text


@pytest.mark.parametrize("apply", [False, True])
async def test_empty_bucket(apply: bool) -> None:
    report = await _gc(FakeStorage([]), apply=apply)
    assert report.orphans == []
    assert "nothing to collect" in format_report(report)
