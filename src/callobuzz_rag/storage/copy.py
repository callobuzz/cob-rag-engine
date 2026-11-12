"""Copy every object of a bucket from one storage to another (S3 to local folder, or back).

    python -m callobuzz_rag.storage.copy --from s3 --to local [--bucket NAME] [--apply]

Dry run by default: it lists what would be copied and writes nothing. With `--apply` it
copies the objects that are missing on the target or whose size differs, never deletes
anything on either side, and can be run again safely (a second run copies nothing).

The `s3` side is built from the `RAG_S3_*` settings (any S3-compatible service, for
example a MinIO server you used before) and the `local` side from
`RAG_STORAGE_LOCAL_ROOT`, whatever `RAG_STORAGE_PROVIDER` says. It prints counts, never a
key or a secret.
"""

import argparse
import asyncio
import io
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from callobuzz_rag.storage.base import ObjectStorageProvider

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CopyResult:
    to_copy: int = 0
    bytes: int = 0
    skipped_same: int = 0
    copied: int = 0
    failed: int = 0


async def copy_bucket(
    source: ObjectStorageProvider,
    target: ObjectStorageProvider,
    bucket: str,
    *,
    apply: bool,
) -> CopyResult:
    """Copy what is missing or different from `source` to `target`; see the module docstring."""
    wanted = await source.list_objects(bucket)
    have = {o.key: o.size for o in await target.list_objects(bucket)}
    todo = [o for o in wanted if have.get(o.key) != o.size]
    skipped = len(wanted) - len(todo)
    total = sum(o.size for o in todo)
    if not apply:
        return CopyResult(to_copy=len(todo), bytes=total, skipped_same=skipped)
    if todo:
        await target.ensure_bucket(bucket)
    copied = 0
    failed = 0
    for obj in todo:
        try:
            data = await source.download(bucket, obj.key)
            await target.upload(bucket, obj.key, io.BytesIO(data))
            copied += 1
        except Exception as e:
            failed += 1
            logger.error("Could not copy %s/%s: %s", bucket, obj.key, e)
    return CopyResult(
        to_copy=len(todo), bytes=total, skipped_same=skipped, copied=copied, failed=failed
    )


def _build(kind: str, settings: "Settings") -> ObjectStorageProvider:
    if kind == "local":
        from callobuzz_rag.storage.factory import local_root
        from callobuzz_rag.storage.local import LocalFileStorageProvider

        return LocalFileStorageProvider(local_root(settings))
    from callobuzz_rag.storage.s3 import S3ObjectStorageProvider

    return S3ObjectStorageProvider(settings)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m callobuzz_rag.storage.copy",
        description=(
            "Copy a bucket between S3-compatible storage and the local files folder. "
            "Dry run unless --apply; never deletes."
        ),
    )
    parser.add_argument("--from", dest="source", choices=["s3", "local"], required=True)
    parser.add_argument("--to", dest="target", choices=["s3", "local"], required=True)
    parser.add_argument("--bucket", default=None, help="bucket name (default: RAG_S3_BUCKET)")
    parser.add_argument("--apply", action="store_true", help="copy for real (default: dry run)")
    args = parser.parse_args(argv)
    if args.source == args.target:
        parser.error("--from and --to must be different")

    from callobuzz_rag.core.config import get_settings

    settings = get_settings()
    bucket = args.bucket or settings.s3_bucket
    try:
        source = _build(args.source, settings)
        target = _build(args.target, settings)
        result = asyncio.run(copy_bucket(source, target, bucket, apply=args.apply))
    except Exception as e:
        print(f"Copy stopped: {e}")
        return 1

    mb = result.bytes / (1024 * 1024)
    if not args.apply:
        print(
            f"Dry run: {result.to_copy} object(s) ({mb:.1f} MB) would be copied from "
            f"{args.source} to {args.target}; {result.skipped_same} already there. "
            "Nothing was written. Run again with --apply to copy."
        )
        return 0
    print(
        f"Copied {result.copied} of {result.to_copy} object(s) ({mb:.1f} MB) from "
        f"{args.source} to {args.target}; {result.skipped_same} were already there."
    )
    if result.failed:
        print(f"{result.failed} object(s) failed; see the log above. Run again to retry them.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
