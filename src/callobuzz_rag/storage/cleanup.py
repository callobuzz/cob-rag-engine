"""Deleting a corpus's or workspace's stored files, off the request path.

A DELETE commits its rows first and then calls `schedule_prefix_cleanup`, which queues
one `delete_storage_prefix` Celery task per folder prefix (`corpus/{id}/`,
`portal/{id}/`, `portal/{corpus}/{workspace}/`). The task deletes with a bounded
storage client (`factory.cleanup_object_storage`) and retries a bounded number of times with backoff. Nothing
here raises into the request: a prefix that cannot be queued, or whose task gives up,
is an ERROR log naming the bucket and prefix, and `python -m callobuzz_rag.storage.gc`
finds and removes whatever such failures leave behind.
"""

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

CLEANUP_CONNECT_TIMEOUT = 5.0  # seconds
CLEANUP_READ_TIMEOUT = 30.0  # seconds, per response (one DeleteObjects of <= 1000 keys)
CLEANUP_MAX_ATTEMPTS = 3  # botocore attempts per call, first one included
_PUBLISH_TIMEOUT = 10.0  # seconds to hand one task to the broker

GC_HINT = "python -m callobuzz_rag.storage.gc --apply"


def _task() -> Any:
    from callobuzz_rag.worker.tasks import delete_storage_prefix_task

    return delete_storage_prefix_task


async def schedule_prefix_cleanup(bucket: str, prefixes: list[str], *, reason: str) -> bool:
    """Queue the deletion of every object under each prefix. Never raises.

    Returns whether every prefix was queued. Publishing retries at most twice within
    about a second, so a dead broker cannot hold the request.
    """
    task = _task()
    queued = True
    for prefix in prefixes:
        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    task.apply_async,
                    kwargs={"bucket": bucket, "prefix": prefix, "reason": reason},
                    retry=True,
                    retry_policy={
                        "max_retries": 2,
                        "interval_start": 0,
                        "interval_step": 0.5,
                        "interval_max": 0.5,
                    },
                ),
                timeout=_PUBLISH_TIMEOUT,
            )
        except Exception:
            queued = False
            logger.error(
                "Stored files under %s/%s were NOT deleted (%s): the cleanup task could not "
                "be queued. They are orphans until removed; run: %s",
                bucket,
                prefix,
                reason,
                GC_HINT,
                exc_info=True,
            )
    return queued


async def discard_object(storage: Any, bucket: str, key: str, *, reason: str) -> None:
    """Delete a file whose row was never committed. Never raises."""
    try:
        await storage.delete(bucket, key)
    except Exception:
        logger.error(
            "Stored file %s/%s has no row (%s) and could not be deleted; it is an orphan "
            "until removed. Run: %s",
            bucket,
            key,
            reason,
            GC_HINT,
            exc_info=True,
        )
