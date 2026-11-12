"""Stored-file cleanup after a corpus or workspace is deleted (M6).

The request used to delete the files itself, inline and unbounded: a slow or
unreachable S3 held the DELETE for botocore's default ~60 s x retries, and a failure
was a one-line WARNING with no traceback. Now the request queues one Celery task per
prefix; the task uses a bounded S3 client and retries a bounded number of times, and a
final failure is an ERROR naming the prefix and the garbage-collection command.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.core.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        storage_provider="s3",
        s3_endpoint="http://127.0.0.1:9002",
        s3_access_key="k",
        s3_secret_key="s",
        s3_bucket="rag",
    )


# --- the bounded client ----------------------------------------------------------


@patch("callobuzz_rag.storage.s3.boto3")
def test_cleanup_client_is_bounded(mock_boto3: MagicMock, settings: Settings) -> None:
    from callobuzz_rag.storage.cleanup import (
        CLEANUP_CONNECT_TIMEOUT,
        CLEANUP_MAX_ATTEMPTS,
        CLEANUP_READ_TIMEOUT,
    )
    from callobuzz_rag.storage.factory import cleanup_object_storage

    cleanup_object_storage(settings)
    config = mock_boto3.client.call_args.kwargs["config"]
    assert config.connect_timeout == CLEANUP_CONNECT_TIMEOUT <= 10
    assert config.read_timeout == CLEANUP_READ_TIMEOUT <= 60
    assert config.retries == {"max_attempts": CLEANUP_MAX_ATTEMPTS, "mode": "standard"}
    # The portability settings stay.
    assert config.request_checksum_calculation == "when_required"


@patch("callobuzz_rag.storage.s3.boto3")
def test_default_client_keeps_botocore_defaults(mock_boto3: MagicMock, settings: Settings) -> None:
    from callobuzz_rag.storage.s3 import S3ObjectStorageProvider

    S3ObjectStorageProvider(settings)
    config = mock_boto3.client.call_args.kwargs["config"]
    assert config.retries is None


# --- queueing from a request -------------------------------------------------------


async def test_each_prefix_is_queued(settings: Settings) -> None:
    from callobuzz_rag.storage import cleanup

    task = MagicMock()
    with patch.object(cleanup, "_task", return_value=task):
        queued = await cleanup.schedule_prefix_cleanup(
            "rag", ["corpus/c1/", "portal/c1/"], reason="corpus c1 deleted"
        )
    assert queued is True
    assert [c.kwargs["kwargs"] for c in task.apply_async.call_args_list] == [
        {"bucket": "rag", "prefix": "corpus/c1/", "reason": "corpus c1 deleted"},
        {"bucket": "rag", "prefix": "portal/c1/", "reason": "corpus c1 deleted"},
    ]
    policy = task.apply_async.call_args.kwargs["retry_policy"]
    assert policy["max_retries"] <= 3  # publishing never hangs on a dead broker


async def test_a_broker_failure_is_an_error_naming_the_prefix(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    from callobuzz_rag.storage import cleanup

    task = MagicMock()
    task.apply_async.side_effect = [ConnectionError("redis down"), None]
    with (
        patch.object(cleanup, "_task", return_value=task),
        caplog.at_level(logging.ERROR, logger="callobuzz_rag.storage.cleanup"),
    ):
        queued = await cleanup.schedule_prefix_cleanup(
            "rag", ["corpus/c1/", "portal/c1/"], reason="corpus c1 deleted"
        )
    assert queued is False
    assert task.apply_async.call_count == 2  # the second prefix is still queued
    (record,) = caplog.records
    assert "rag/corpus/c1/" in record.getMessage()
    assert "callobuzz_rag.storage.gc" in record.getMessage()
    assert record.exc_info is not None


# --- the task ------------------------------------------------------------------


def _run_task(retries: int, delete: AsyncMock):
    from callobuzz_rag.worker.tasks import delete_storage_prefix_task

    storage = MagicMock()
    storage.delete_prefix = delete
    delete_storage_prefix_task.push_request(retries=retries)
    try:
        with patch("callobuzz_rag.storage.factory.cleanup_object_storage", return_value=storage):
            return delete_storage_prefix_task.run("rag", "portal/c1/w1/", "workspace deleted")
    finally:
        delete_storage_prefix_task.pop_request()


def test_task_deletes_the_prefix() -> None:
    result = _run_task(0, AsyncMock(return_value=4))
    assert result == {"status": "deleted", "prefix": "portal/c1/w1/", "objects": 4}


def test_task_retries_a_bounded_number_of_times() -> None:
    from celery.exceptions import Retry

    from callobuzz_rag.worker.tasks import delete_storage_prefix_task

    assert 1 <= delete_storage_prefix_task.max_retries <= 10
    with (
        patch.object(delete_storage_prefix_task, "retry", side_effect=Retry()) as retry,
        pytest.raises(Retry),
    ):
        _run_task(1, AsyncMock(side_effect=TimeoutError("s3 slow")))
    assert retry.call_args.kwargs["countdown"] == 60


def test_task_gives_up_with_an_error_naming_the_prefix(caplog: pytest.LogCaptureFixture) -> None:
    from callobuzz_rag.worker.tasks import delete_storage_prefix_task

    with caplog.at_level(logging.ERROR, logger="callobuzz_rag.worker.tasks"):
        result = _run_task(
            delete_storage_prefix_task.max_retries, AsyncMock(side_effect=TimeoutError("down"))
        )
    assert result["status"] == "failed"
    (record,) = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert "rag/portal/c1/w1/" in record.getMessage()
    assert "callobuzz_rag.storage.gc" in record.getMessage()
    assert record.exc_info is not None


def test_task_refuses_a_bucket_wide_prefix_without_retrying(
    caplog: pytest.LogCaptureFixture,
) -> None:
    result = _run_task(0, AsyncMock(side_effect=ValueError("needs a folder prefix")))
    assert result["status"] == "failed"
