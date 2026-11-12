"""Files in a folder on the server: the default object storage for a new install.

A bucket is a folder under the root and a key is a path inside it. Every path is
checked before it is touched, because keys come from file names and ids that
originate in a request:

* the bucket is one short lower-case segment;
* the key has no backslash, NUL, drive letter, leading slash, or empty, ``.`` or
  ``..`` segment;
* the resolved path must still be inside the bucket folder, which also stops a
  symlink that points outside it.

Writes go to a temporary file in the same folder and are moved into place with
``os.replace``, so a reader never sees half a file. One server only: several app
hosts need ``RAG_STORAGE_PROVIDER=s3``.
"""

import asyncio
import contextlib
import logging
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from callobuzz_rag.core.exceptions import StorageError
from callobuzz_rag.storage.base import ObjectStorageProvider, StoredObject

logger = logging.getLogger(__name__)

_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{0,62}$")
_DRIVE = re.compile(r"^[A-Za-z]:")
_TEMP_PREFIX = ".tmp-"


class LocalFileStorageProvider(ObjectStorageProvider):
    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()
        # A root that cannot be made (a file is in the way, no permission) is reported by
        # `bucket_exists` and by the first write, not here: building the provider must not fail.
        with contextlib.suppress(OSError):
            self._root.mkdir(parents=True, exist_ok=True)

    # --- paths -------------------------------------------------------------------------

    def _bucket_dir(self, bucket: str) -> Path:
        if not _BUCKET.fullmatch(bucket or "") or bucket in (".", ".."):
            raise StorageError(f"invalid bucket name: {bucket!r}")
        return self._root / bucket

    @staticmethod
    def _check_key(key: str) -> None:
        bad = (
            not key
            or "\\" in key
            or "\x00" in key
            or key.startswith("/")
            or _DRIVE.match(key) is not None
            or any(part in ("", ".", "..") for part in key.split("/"))
        )
        if bad:
            raise StorageError(f"invalid object key: {key!r}")

    def _path(self, bucket: str, key: str) -> Path:
        base = self._bucket_dir(bucket)
        self._check_key(key)
        candidate = (base / key).resolve()
        if not candidate.is_relative_to(base.resolve()):
            raise StorageError(f"invalid object key: {key!r}")
        return candidate

    # --- the provider interface -----------------------------------------------------------

    async def upload(
        self, bucket: str, key: str, data: BinaryIO, content_type: str | None = None
    ) -> None:
        path = self._path(bucket, key)
        logger.info("Storing %s/%s", bucket, key)

        def _run() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=_TEMP_PREFIX)
            try:
                with os.fdopen(fd, "wb") as out:
                    while chunk := data.read(1024 * 1024):
                        out.write(chunk)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(tmp, path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise

        try:
            await asyncio.to_thread(_run)
        except OSError as e:
            raise StorageError(f"could not store {bucket}/{key}: {e.strerror or e}") from e

    async def download(self, bucket: str, key: str) -> bytes:
        path = self._path(bucket, key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as e:
            raise StorageError(f"object not found: {bucket}/{key}") from e
        except OSError as e:
            raise StorageError(f"could not read {bucket}/{key}: {e.strerror or e}") from e

    async def delete(self, bucket: str, key: str) -> None:
        path = self._path(bucket, key)

        def _run() -> None:
            with contextlib.suppress(FileNotFoundError):
                path.unlink()

        await asyncio.to_thread(_run)

    async def delete_prefix(self, bucket: str, prefix: str) -> int:
        """Delete every object under a folder prefix such as ``corpus/<id>/``.

        Same contract as the S3 provider: only a non-empty prefix ending in "/" is accepted,
        and it is a folder boundary (``corpus/c1/`` never touches ``corpus/c10/``).
        """
        if not prefix.endswith("/") or not prefix.strip("/"):
            raise ValueError(
                f"delete_prefix needs a folder prefix like 'corpus/<id>/', got {prefix!r}"
            )
        base = self._bucket_dir(bucket).resolve()
        folder = self._path(bucket, prefix.rstrip("/"))

        def _run() -> int:
            if not folder.is_dir():
                return 0
            deleted = 0
            for dirpath, _dirs, files in os.walk(folder, topdown=False, followlinks=False):
                for name in files:
                    # A half-written upload (temp file) goes too; it is not counted as an object.
                    os.unlink(os.path.join(dirpath, name))
                    if not name.startswith(_TEMP_PREFIX):
                        deleted += 1
                with contextlib.suppress(OSError):  # not empty: another upload just landed
                    os.rmdir(dirpath)
            parent = folder.parent
            while parent != base and base in parent.parents:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
            return deleted

        count = await asyncio.to_thread(_run)
        logger.info("Deleted %d objects under %s/%s", count, bucket, prefix)
        return count

    async def list_objects(self, bucket: str, prefix: str = "") -> list[StoredObject]:
        base = self._bucket_dir(bucket)
        if prefix and (
            "\\" in prefix
            or "\x00" in prefix
            or prefix.startswith("/")
            or ".." in prefix.split("/")
        ):
            raise StorageError(f"invalid object key: {prefix!r}")

        def _run() -> list[StoredObject]:
            found: list[StoredObject] = []
            if not base.is_dir():
                return found
            stack = [base]
            while stack:
                current = stack.pop()
                with os.scandir(current) as entries:
                    for entry in entries:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False) and not entry.name.startswith(
                            _TEMP_PREFIX
                        ):
                            key = Path(entry.path).relative_to(base).as_posix()
                            if not key.startswith(prefix):
                                continue
                            st = entry.stat(follow_symlinks=False)
                            found.append(
                                StoredObject(
                                    key=key,
                                    size=st.st_size,
                                    last_modified=datetime.fromtimestamp(st.st_mtime, tz=UTC),
                                )
                            )
            return sorted(found, key=lambda o: o.key)

        return await asyncio.to_thread(_run)

    async def exists(self, bucket: str, key: str) -> bool:
        path = self._path(bucket, key)
        return await asyncio.to_thread(path.is_file)

    async def get_presigned_url(self, bucket: str, key: str, expires_in: int = 3600) -> str:
        raise StorageError("presigned URLs are not available with local storage")

    async def ensure_bucket(self, bucket: str) -> None:
        base = self._bucket_dir(bucket)
        try:
            await asyncio.to_thread(base.mkdir, parents=True, exist_ok=True)
        except OSError as e:
            raise StorageError(f"could not create {base}: {e.strerror or e}") from e

    async def bucket_exists(self, bucket: str) -> bool:
        """Whether the bucket folder is there (made if missing) and writable."""
        base = self._bucket_dir(bucket)

        def _run() -> bool:
            try:
                base.mkdir(parents=True, exist_ok=True)
            except OSError:
                return False
            return base.is_dir() and os.access(base, os.W_OK)

        return await asyncio.to_thread(_run)
