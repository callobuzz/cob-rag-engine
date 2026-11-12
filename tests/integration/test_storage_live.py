"""Object storage against a REAL S3 endpoint — no mocks anywhere.

Runs against whatever RAG_S3_* points at: an S3 server you run yourself, or a real
R2 / AWS / Supabase bucket if you export those variables before running. That is
the point — this is the test that answers "will it work on the provider we
actually deploy against", which a mocked boto3 can never answer.

    pytest tests/integration/test_storage_live.py -m integration

Skips (does not fail) when no S3 credentials are exported or the endpoint is unreachable, so it
is safe in a run with no infrastructure: since L2 the default install keeps files on the server
(RAG_STORAGE_PROVIDER=local) and runs no S3 service, so there is nothing to test against unless
you point it at one.
"""

import asyncio
import io
import uuid

import pytest

from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.exceptions import StorageError
from callobuzz_rag.storage.s3 import S3ObjectStorageProvider

pytestmark = pytest.mark.integration


@pytest.fixture
async def provider() -> S3ObjectStorageProvider:
    settings = Settings()
    try:
        storage = S3ObjectStorageProvider(settings)
    except StorageError as e:
        pytest.skip(f"No S3 storage configured (export RAG_S3_* to run this test): {e}")
    try:
        await storage.bucket_exists(settings.s3_bucket)
        # bucket_exists answers True on a 403 (a credential scoped to objects only),
        # so a bucket that cannot exist tells a rejected credential apart.
        rejected = await storage.bucket_exists(f"rag-probe-{uuid.uuid4().hex[:12]}")
    except Exception as e:
        pytest.skip(f"No object storage at {settings.s3_endpoint}: {e}")
    if rejected:
        pytest.skip(f"{settings.s3_endpoint} refused the RAG_S3_* credentials; export them")
    return storage


async def test_bucket_lifecycle_and_roundtrip(provider: S3ObjectStorageProvider) -> None:
    bucket = f"rag-live-test-{uuid.uuid4().hex[:12]}"
    key = "nested/path/probe.txt"
    payload = b"real bytes through a real S3 API"

    assert await provider.bucket_exists(bucket) is False

    # ensure_bucket on a missing bucket creates it; called twice it is a no-op,
    # which is what makes it safe to run on every startup.
    await provider.ensure_bucket(bucket)
    await provider.ensure_bucket(bucket)
    assert await provider.bucket_exists(bucket) is True

    try:
        # Upload is the operation the botocore >= 1.36 checksum default breaks.
        # If this passes against a non-AWS endpoint, the portability config is
        # doing its job.
        await provider.upload(bucket, key, io.BytesIO(payload), content_type="text/plain")
        assert await provider.exists(bucket, key) is True
        assert await provider.download(bucket, key) == payload

        url = await provider.get_presigned_url(bucket, key, expires_in=60)
        assert bucket in url or key in url

        await provider.delete(bucket, key)
        assert await provider.exists(bucket, key) is False
    finally:
        # Test-only teardown. Deliberately reaches the boto3 client directly
        # rather than adding a drop_bucket to ObjectStorageProvider: nothing in
        # production deletes a bucket, and an interface should not carry a
        # destructive method solely so a test can tidy up after itself.
        await asyncio.to_thread(provider._client.delete_object, Bucket=bucket, Key=key)
        await asyncio.to_thread(provider._client.delete_bucket, Bucket=bucket)

    assert await provider.bucket_exists(bucket) is False
