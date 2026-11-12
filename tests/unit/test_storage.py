"""Unit tests for S3ObjectStorageProvider.

boto3 is mocked (there is no S3 in a unit run) but Settings is REAL. A MagicMock
settings object answers every attribute, including ones that do not exist, so it
cannot catch a typo'd setting name or a setting that was never added — which is
exactly how three runtime bugs got into this codebase. Constructing the real
Settings also means the BotoConfig built from it is the one production builds.
"""

import io
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from callobuzz_rag.core.config import Settings
from callobuzz_rag.storage.s3 import S3ObjectStorageProvider


@pytest.fixture
def settings() -> Settings:
    return Settings(
        s3_endpoint="http://127.0.0.1:9002",
        s3_access_key="k",
        s3_secret_key="s",
        s3_bucket="rag-documents",
        s3_region="us-east-1",
    )


def _client(mock_boto3: MagicMock) -> MagicMock:
    return mock_boto3.client.return_value  # type: ignore[no-any-return]


def _client_error(code: str, op: str) -> ClientError:
    return ClientError({"Error": {"Code": code}}, op)


@patch("callobuzz_rag.storage.s3.boto3")
async def test_upload(mock_boto3: MagicMock, settings: Settings) -> None:
    provider = S3ObjectStorageProvider(settings)
    await provider.upload("bucket", "key", io.BytesIO(b"test content"), content_type="text/plain")
    _client(mock_boto3).upload_fileobj.assert_called_once()


@patch("callobuzz_rag.storage.s3.boto3")
async def test_exists_true(mock_boto3: MagicMock, settings: Settings) -> None:
    provider = S3ObjectStorageProvider(settings)
    assert await provider.exists("bucket", "key") is True


@patch("callobuzz_rag.storage.s3.boto3")
async def test_exists_false(mock_boto3: MagicMock, settings: Settings) -> None:
    _client(mock_boto3).head_object.side_effect = _client_error("404", "HeadObject")
    provider = S3ObjectStorageProvider(settings)
    assert await provider.exists("bucket", "missing-key") is False


@patch("callobuzz_rag.storage.s3.boto3")
async def test_delete(mock_boto3: MagicMock, settings: Settings) -> None:
    provider = S3ObjectStorageProvider(settings)
    await provider.delete("bucket", "key")
    _client(mock_boto3).delete_object.assert_called_once_with(Bucket="bucket", Key="key")


# --- Portability: the config that makes "any S3 provider" true -----------------


@patch("callobuzz_rag.storage.s3.boto3")
async def test_checksum_calculation_is_when_required(
    mock_boto3: MagicMock, settings: Settings
) -> None:
    """botocore >= 1.36 defaults to when_supported, which many gateways reject.

    The default attaches a CRC32 checksum and `aws-chunked` framing to every
    PutObject. AWS accepts it; Supabase Storage, older MinIO, B2 and Spaces
    return an opaque signature mismatch instead. Regressing this line breaks
    every non-AWS provider at upload time and nowhere earlier.
    """
    S3ObjectStorageProvider(settings)
    config = mock_boto3.client.call_args.kwargs["config"]
    assert config.request_checksum_calculation == "when_required"
    assert config.response_checksum_validation == "when_required"


@patch("callobuzz_rag.storage.s3.boto3")
async def test_addressing_style_is_taken_from_settings(mock_boto3: MagicMock) -> None:
    provider_settings = Settings(s3_addressing_style="path", s3_access_key="k", s3_secret_key="s")
    S3ObjectStorageProvider(provider_settings)
    config = mock_boto3.client.call_args.kwargs["config"]
    assert config.s3["addressing_style"] == "path"


# --- ensure_bucket -------------------------------------------------------------


@patch("callobuzz_rag.storage.s3.boto3")
async def test_ensure_bucket_existing_does_not_create(
    mock_boto3: MagicMock, settings: Settings
) -> None:
    """HEAD succeeds -> no CreateBucket call at all.

    Calling CreateBucket unconditionally is what the old implementation did, and
    it fails for a credential scoped to one existing bucket with no create
    permission — the normal shape of a hardened production key.
    """
    provider = S3ObjectStorageProvider(settings)
    await provider.ensure_bucket("my-bucket")
    _client(mock_boto3).create_bucket.assert_not_called()


@patch("callobuzz_rag.storage.s3.boto3")
async def test_ensure_bucket_creates_when_missing(
    mock_boto3: MagicMock, settings: Settings
) -> None:
    _client(mock_boto3).head_bucket.side_effect = _client_error("404", "HeadBucket")
    provider = S3ObjectStorageProvider(settings)
    await provider.ensure_bucket("my-bucket")
    # us-east-1 -> no CreateBucketConfiguration; AWS rejects it for that region.
    _client(mock_boto3).create_bucket.assert_called_once_with(Bucket="my-bucket")


@patch("callobuzz_rag.storage.s3.boto3")
async def test_ensure_bucket_sends_location_constraint_for_real_regions(
    mock_boto3: MagicMock,
) -> None:
    _client(mock_boto3).head_bucket.side_effect = _client_error("404", "HeadBucket")
    provider = S3ObjectStorageProvider(
        Settings(s3_region="ap-south-1", s3_access_key="k", s3_secret_key="s")
    )
    await provider.ensure_bucket("my-bucket")
    _client(mock_boto3).create_bucket.assert_called_once_with(
        Bucket="my-bucket",
        CreateBucketConfiguration={"LocationConstraint": "ap-south-1"},
    )


@patch("callobuzz_rag.storage.s3.boto3")
async def test_ensure_bucket_omits_location_constraint_for_r2(mock_boto3: MagicMock) -> None:
    """R2 requires region "auto", which is not a location constraint.

    Sending it as one produces InvalidLocationConstraint and the bucket is never
    created.
    """
    _client(mock_boto3).head_bucket.side_effect = _client_error("404", "HeadBucket")
    provider = S3ObjectStorageProvider(
        Settings(s3_region="auto", s3_access_key="k", s3_secret_key="s")
    )
    await provider.ensure_bucket("my-bucket")
    _client(mock_boto3).create_bucket.assert_called_once_with(Bucket="my-bucket")


@patch("callobuzz_rag.storage.s3.boto3")
async def test_ensure_bucket_tolerates_forbidden_head(
    mock_boto3: MagicMock, settings: Settings
) -> None:
    """403 means it exists but we may not inspect it — not a reason to fail."""
    _client(mock_boto3).head_bucket.side_effect = _client_error("403", "HeadBucket")
    provider = S3ObjectStorageProvider(settings)
    await provider.ensure_bucket("my-bucket")
    _client(mock_boto3).create_bucket.assert_not_called()


@patch("callobuzz_rag.storage.s3.boto3")
async def test_ensure_bucket_survives_create_race(
    mock_boto3: MagicMock, settings: Settings
) -> None:
    _client(mock_boto3).head_bucket.side_effect = _client_error("404", "HeadBucket")
    _client(mock_boto3).create_bucket.side_effect = _client_error(
        "BucketAlreadyOwnedByYou", "CreateBucket"
    )
    provider = S3ObjectStorageProvider(settings)
    await provider.ensure_bucket("my-bucket")  # must not raise


@patch("callobuzz_rag.storage.s3.boto3")
async def test_ensure_bucket_propagates_real_errors(
    mock_boto3: MagicMock, settings: Settings
) -> None:
    """A bad endpoint or wrong key must not be swallowed as "bucket missing"."""
    _client(mock_boto3).head_bucket.side_effect = _client_error("InvalidAccessKeyId", "HeadBucket")
    provider = S3ObjectStorageProvider(settings)
    with pytest.raises(ClientError):
        await provider.ensure_bucket("my-bucket")


# --- bucket_exists -------------------------------------------------------------


@patch("callobuzz_rag.storage.s3.boto3")
async def test_bucket_exists_variants(mock_boto3: MagicMock, settings: Settings) -> None:
    provider = S3ObjectStorageProvider(settings)
    assert await provider.bucket_exists("b") is True

    _client(mock_boto3).head_bucket.side_effect = _client_error("404", "HeadBucket")
    assert await provider.bucket_exists("b") is False

    _client(mock_boto3).head_bucket.side_effect = _client_error("403", "HeadBucket")
    assert await provider.bucket_exists("b") is True


@patch("callobuzz_rag.storage.s3.boto3")
async def test_delete_prefix_removes_every_page(mock_boto3: MagicMock, settings: Settings) -> None:
    """A deleted corpus's files, however many: list page by page, delete in batches."""
    pages = [
        {"Contents": [{"Key": f"corpus/c1/{i}"} for i in range(1000)]},
        {"Contents": [{"Key": "corpus/c1/last"}]},
    ]
    paginator = _client(mock_boto3).get_paginator.return_value
    paginator.paginate.return_value = pages
    provider = S3ObjectStorageProvider(settings)

    deleted = await provider.delete_prefix("bucket", "corpus/c1/")

    assert deleted == 1001
    _client(mock_boto3).get_paginator.assert_called_once_with("list_objects_v2")
    paginator.paginate.assert_called_once_with(Bucket="bucket", Prefix="corpus/c1/")
    calls = _client(mock_boto3).delete_objects.call_args_list
    assert [len(c.kwargs["Delete"]["Objects"]) for c in calls] == [1000, 1]
    assert all(c.kwargs["Bucket"] == "bucket" for c in calls)


@patch("callobuzz_rag.storage.s3.boto3")
async def test_delete_prefix_with_nothing_there(mock_boto3: MagicMock, settings: Settings) -> None:
    _client(mock_boto3).get_paginator.return_value.paginate.return_value = [{"KeyCount": 0}]
    provider = S3ObjectStorageProvider(settings)
    assert await provider.delete_prefix("bucket", "portal/c1/") == 0
    _client(mock_boto3).delete_objects.assert_not_called()


@pytest.mark.parametrize("prefix", ["", "corpus", "/"])
@patch("callobuzz_rag.storage.s3.boto3")
async def test_delete_prefix_refuses_a_whole_bucket_wipe(
    mock_boto3: MagicMock, settings: Settings, prefix: str
) -> None:
    """Only a folder prefix ("x/") is accepted: an empty or bare one could match everything."""
    provider = S3ObjectStorageProvider(settings)
    with pytest.raises(ValueError, match="folder prefix"):
        await provider.delete_prefix("bucket", prefix)
    _client(mock_boto3).get_paginator.assert_not_called()


@patch("callobuzz_rag.storage.s3.boto3")
async def test_delete_prefix_reports_objects_s3_refused(
    mock_boto3: MagicMock, settings: Settings
) -> None:
    _client(mock_boto3).get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": "corpus/c1/a"}]}
    ]
    _client(mock_boto3).delete_objects.return_value = {
        "Errors": [{"Key": "corpus/c1/a", "Code": "AccessDenied"}]
    }
    provider = S3ObjectStorageProvider(settings)
    with pytest.raises(RuntimeError, match="1 object"):
        await provider.delete_prefix("bucket", "corpus/c1/")


@patch("callobuzz_rag.storage.s3.boto3")
async def test_list_objects_reads_every_page(mock_boto3: MagicMock, settings: Settings) -> None:
    from datetime import UTC, datetime

    from callobuzz_rag.storage.base import StoredObject

    when = datetime(2026, 9, 30, tzinfo=UTC)
    _client(mock_boto3).get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": "corpus/a/1", "Size": 3, "LastModified": when}]},
        {},
        {"Contents": [{"Key": "corpus/a/2", "Size": 5, "LastModified": when}]},
    ]
    listed = await S3ObjectStorageProvider(settings).list_objects("bucket", "corpus/")
    assert listed == [
        StoredObject("corpus/a/1", 3, when),
        StoredObject("corpus/a/2", 5, when),
    ]
    _client(mock_boto3).get_paginator.return_value.paginate.assert_called_once_with(
        Bucket="bucket", Prefix="corpus/"
    )
