"""Find, and optionally delete, stored files that no corpus or workspace owns any more.

    python -m callobuzz_rag.storage.gc            # dry run: list what would go
    python -m callobuzz_rag.storage.gc --apply    # delete it (prints the list first)

The engine stores files under three folder prefixes, all derived from rows:
``corpus/{corpus_id}/`` (uploaded documents), ``portal/{corpus_id}/`` (Content Portal
staged uploads) and, inside it, ``portal/{corpus_id}/{workspace_id}/`` (one workspace's).
Deleting a corpus or workspace queues their removal, but files can be left behind: by
engines before 0.2 (which never removed them), by a cleanup task that gave up, or by an
upload that raced a delete on an older engine. This command lists every such prefix
whose corpus (or workspace) no longer exists in Postgres, with object counts and sizes.

Safe to run on a live system:
- only keys in the layouts above are considered; anything else in the bucket is left
  alone and only counted;
- a prefix holding any object younger than ``--min-age-minutes`` (default 60) is skipped
  this time, because it may belong to a row that is not committed yet;
- the database is read AFTER the bucket is listed, so a corpus created mid-run is seen.
"""

import argparse
import asyncio
import logging
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.storage.base import StoredObject

logger = logging.getLogger(__name__)

DEFAULT_MIN_AGE = timedelta(minutes=60)


class _Storage(Protocol):
    async def list_objects(self, bucket: str, prefix: str = "") -> "list[StoredObject]": ...

    async def delete_prefix(self, bucket: str, prefix: str) -> int: ...


@dataclass
class Orphan:
    prefix: str
    reason: str  # "corpus deleted" | "workspace deleted"
    objects: int = 0
    bytes: int = 0
    newest: datetime | None = None
    deleted: int | None = None
    error: str | None = None


@dataclass
class GcReport:
    bucket: str
    orphans: list[Orphan] = field(default_factory=list)
    applied: bool = False
    unrecognised: int = 0  # objects outside the engine's layouts (never touched)
    too_recent: int = 0  # orphan prefixes skipped because an object in them is young

    @property
    def failed(self) -> int:
        return sum(1 for o in self.orphans if o.error)


def _uuid(part: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(part)
    except ValueError:
        return None


def _owner(key: str, corpora: set[uuid.UUID], workspaces: set[uuid.UUID]) -> tuple[str, str] | None:
    """(prefix, reason) when `key` is in an engine layout whose owner is gone; else None.

    Raises:
        LookupError: The key is not in a layout the engine writes.
    """
    parts = key.split("/")
    # P2 invoices: `billing/tax-documents/...` (GST invoice PDFs, kept for years) is not one of
    # the layouts above, so it is only ever counted as unrecognised, never collected.
    if len(parts) < 3 or parts[0] not in ("corpus", "portal"):
        raise LookupError(key)
    corpus_id = _uuid(parts[1])
    if corpus_id is None:
        raise LookupError(key)
    if corpus_id not in corpora:
        return f"{parts[0]}/{corpus_id}/", "corpus deleted"
    if parts[0] == "portal" and len(parts) >= 4:
        workspace_id = _uuid(parts[2])
        if workspace_id is None:
            raise LookupError(key)
        if workspace_id not in workspaces:
            return f"portal/{corpus_id}/{workspace_id}/", "workspace deleted"
    return None


async def collect_garbage(
    storage: _Storage,
    bucket: str,
    *,
    corpora: set[uuid.UUID],
    workspaces: set[uuid.UUID],
    apply: bool = False,
    min_age: timedelta = DEFAULT_MIN_AGE,
    now: datetime | None = None,
    objects: "list[StoredObject] | None" = None,
) -> GcReport:
    """Group the bucket's orphaned objects by prefix; with `apply`, delete each prefix.

    `objects` is the bucket listing when the caller already has it (it must be taken
    BEFORE `corpora`/`workspaces` are read); otherwise it is listed here.
    """
    now = now or datetime.now(UTC)
    listing = objects if objects is not None else await storage.list_objects(bucket)
    report = GcReport(bucket=bucket, applied=apply)
    found: dict[str, Orphan] = {}
    for obj in listing:
        try:
            owner = _owner(obj.key, corpora, workspaces)
        except LookupError:
            report.unrecognised += 1
            continue
        if owner is None:
            continue
        prefix, reason = owner
        orphan = found.setdefault(prefix, Orphan(prefix=prefix, reason=reason))
        orphan.objects += 1
        orphan.bytes += obj.size
        if orphan.newest is None or obj.last_modified > orphan.newest:
            orphan.newest = obj.last_modified

    for orphan in sorted(found.values(), key=lambda o: o.prefix):
        if orphan.newest is not None and now - orphan.newest < min_age:
            report.too_recent += 1
            continue
        report.orphans.append(orphan)

    if apply:
        for orphan in report.orphans:
            try:
                orphan.deleted = await storage.delete_prefix(bucket, orphan.prefix)
            except Exception as e:  # one prefix failing must not stop the pass
                orphan.error = str(e) or type(e).__name__
                logger.error("Could not delete %s/%s", bucket, orphan.prefix, exc_info=True)
    return report


def _size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"  # pragma: no cover


def format_report(report: GcReport) -> str:
    lines = [f"bucket {report.bucket}"]
    if not report.orphans:
        lines.append("nothing to collect: every stored prefix belongs to a live row")
    else:
        verb = "deleting" if report.applied else "would delete"
        lines.append(f"{verb}:")
        width = max(len(o.prefix) for o in report.orphans)
        for o in report.orphans:
            outcome = ""
            if report.applied:
                outcome = f"  -> FAILED: {o.error}" if o.error else f"  -> deleted {o.deleted}"
            lines.append(
                f"  {o.prefix:<{width}} {o.objects:>6} objects {_size(o.bytes):>10}  ({o.reason})"
                f"{outcome}"
            )
        total_objects = sum(o.objects for o in report.orphans)
        total_bytes = sum(o.bytes for o in report.orphans)
        n = len(report.orphans)
        lines.append(
            f"total: {n} prefix{'es' if n != 1 else ''}, {total_objects} objects, "
            f"{_size(total_bytes)}"
        )
    if report.too_recent:
        lines.append(
            f"skipped {report.too_recent} orphan prefix(es) holding files younger than the "
            "minimum age; run again later"
        )
    if report.unrecognised:
        lines.append(f"left alone: {report.unrecognised} object(s) outside the engine's layouts")
    if report.applied:
        if report.failed:
            lines.append(f"{report.failed} prefix(es) FAILED; run again to retry")
    elif report.orphans:
        lines.append("dry run: nothing deleted. Re-run with --apply to delete the above.")
    return "\n".join(lines)


async def _run(settings: "Settings", apply: bool, min_age: timedelta, bucket: str) -> int:
    from sqlalchemy import select

    from callobuzz_rag.content_portal.models.workspace import Workspace
    from callobuzz_rag.db.engine import create_engine, create_session_factory
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.storage.factory import cleanup_object_storage

    storage = cleanup_object_storage(settings)
    # Listing first, rows second: a file listed here was stored after its row was
    # written, so a row created during the run is still read below.
    objects = await storage.list_objects(bucket)
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as session:
            corpora = set((await session.execute(select(Corpus.id))).scalars().all())
            workspaces = set((await session.execute(select(Workspace.id))).scalars().all())
    finally:
        await engine.dispose()

    dry = await collect_garbage(
        storage,
        bucket,
        corpora=corpora,
        workspaces=workspaces,
        min_age=min_age,
        objects=objects,
    )
    print(format_report(dry))
    if not apply or not dry.orphans:
        return 0
    print()
    applied = await collect_garbage(
        storage,
        bucket,
        corpora=corpora,
        workspaces=workspaces,
        apply=True,
        min_age=min_age,
        objects=objects,
    )
    print(format_report(applied))
    return 1 if applied.failed else 0


def main() -> None:
    from callobuzz_rag.core.config import get_settings

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="delete; without it, a dry run")
    parser.add_argument(
        "--min-age-minutes",
        type=int,
        default=int(DEFAULT_MIN_AGE.total_seconds() // 60),
        help="skip prefixes holding a file younger than this (default 60)",
    )
    parser.add_argument("--bucket", help="bucket to scan (default RAG_S3_BUCKET)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    settings = get_settings()
    code = asyncio.run(
        _run(
            settings,
            args.apply,
            timedelta(minutes=max(0, args.min_age_minutes)),
            args.bucket or settings.s3_bucket,
        )
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
