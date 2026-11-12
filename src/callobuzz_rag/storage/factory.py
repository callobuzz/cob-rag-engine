"""The one place an `ObjectStorageProvider` is built.

`RAG_STORAGE_PROVIDER` picks `local` (files in a folder on this server, the default) or
`s3` (any S3-compatible service). Everything else asks `object_storage(settings)`.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from callobuzz_rag.storage.base import ObjectStorageProvider

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings

logger = logging.getLogger(__name__)

StorageKind = Literal["local", "s3"]


def local_root(settings: "Settings") -> Path:
    """`RAG_STORAGE_LOCAL_ROOT`; a relative one is anchored to the project root, not the CWD."""
    from callobuzz_rag.core.config import _project_env_file

    root = Path(settings.storage_local_root)
    if not root.is_absolute():
        root = _project_env_file().parent / root
    return root


def object_storage(settings: "Settings") -> ObjectStorageProvider:
    if settings.storage_provider == "local":
        from callobuzz_rag.storage.local import LocalFileStorageProvider

        return LocalFileStorageProvider(local_root(settings))
    from callobuzz_rag.storage.s3 import S3ObjectStorageProvider

    return S3ObjectStorageProvider(settings)


def cleanup_object_storage(settings: "Settings") -> ObjectStorageProvider:
    """Storage for background deletion: an S3 client whose every call gives up quickly."""
    if settings.storage_provider == "local":
        return object_storage(settings)
    from callobuzz_rag.storage.cleanup import (
        CLEANUP_CONNECT_TIMEOUT,
        CLEANUP_MAX_ATTEMPTS,
        CLEANUP_READ_TIMEOUT,
    )
    from callobuzz_rag.storage.s3 import S3ObjectStorageProvider

    return S3ObjectStorageProvider(
        settings,
        connect_timeout=CLEANUP_CONNECT_TIMEOUT,
        read_timeout=CLEANUP_READ_TIMEOUT,
        max_attempts=CLEANUP_MAX_ATTEMPTS,
    )


async def storage_reachable(storage: ObjectStorageProvider, bucket: str) -> bool:
    """Health and start-up check: the bucket is there and this process may use it."""
    check = getattr(storage, "bucket_exists", None)
    if check is None:
        return True
    return bool(await check(bucket))
