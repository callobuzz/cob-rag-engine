"""`delete_prefix` against a real S3 server (testcontainers), no mocks.

SeaweedFS is only a test fixture here, an S3 server for the `s3` provider to talk to; it is never
shipped or installed with the engine. (MinIO's images were withdrawn from Docker Hub in 2025.)

DeleteObjects is one of the S3 calls whose API REQUIRES a body checksum (Content-MD5 or
an x-amz-checksum-*). The provider sets `request_checksum_calculation="when_required"`
so uploads work on picky S3 gateways; this proves botocore still attaches the checksum
to DeleteObjects under that setting and the server accepts the request, that listing pages
past 1000 keys, and that a prefix never reaches a sibling that merely starts with the
same characters. Requires Docker.
"""

import asyncio
import io
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator

import pytest

from callobuzz_rag.core.config import Settings
from callobuzz_rag.storage.s3 import S3ObjectStorageProvider

pytestmark = pytest.mark.integration

SEAWEEDFS_IMAGE = "chrislusf/seaweedfs:3.97"  # Apache-2.0; S3 gateway with no identities = open


def _wait_for_s3(url: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            urllib.request.urlopen(url, timeout=2)  # a local test container
            return
        except urllib.error.HTTPError:
            return  # the gateway answers (an S3 error body is still an answer)
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


@pytest.fixture(scope="module")
def s3_settings() -> Iterator[Settings]:
    from testcontainers.core.container import DockerContainer

    container = (
        DockerContainer(SEAWEEDFS_IMAGE)
        .with_command("server -s3 -dir=/data -ip.bind=0.0.0.0")
        .with_exposed_ports(8333)
    )
    with container:
        # 127.0.0.1, never "localhost": on Windows that is ::1 and Docker Desktop resets it.
        endpoint = f"http://127.0.0.1:{container.get_exposed_port(8333)}"
        _wait_for_s3(endpoint)
        yield Settings(
            _env_file=None,
            storage_provider="s3",
            s3_endpoint=endpoint,
            s3_access_key="test-access",
            s3_secret_key="test-secret-key",
            s3_bucket=f"live-{uuid.uuid4().hex[:8]}",
            s3_region="us-east-1",
        )


async def _keys(storage: S3ObjectStorageProvider, bucket: str) -> set[str]:
    def run() -> set[str]:
        paginator = storage._client.get_paginator("list_objects_v2")
        return {
            o["Key"] for page in paginator.paginate(Bucket=bucket) for o in page.get("Contents", [])
        }

    return await asyncio.to_thread(run)


async def _put_many(storage: S3ObjectStorageProvider, bucket: str, keys: list[str]) -> None:
    def run() -> None:
        for key in keys:
            storage._client.put_object(Bucket=bucket, Key=key, Body=b"x")

    await asyncio.to_thread(run)


async def test_delete_prefix_pages_past_1000_and_spares_siblings(s3_settings) -> None:
    storage = S3ObjectStorageProvider(s3_settings)
    bucket = s3_settings.s3_bucket
    await storage.ensure_bucket(bucket)
    doomed = [f"corpus/c1/doc{i:04d}/raw" for i in range(1005)]
    # (No object named exactly "corpus/c1": MinIO stores objects as files, and a file
    # cannot share its name with the folder holding the doomed objects.)
    spared = ["corpus/c10/doc/raw", "corpus/c1-old/raw", "portal/c1/w/raw", "corpus/c2/doc/raw"]
    await _put_many(storage, bucket, doomed + spared)
    # One uploaded through the provider, as ingest stores files.
    await storage.upload(bucket, "corpus/c1/uploaded/raw", io.BytesIO(b"real"), "text/plain")

    sent: list[set[str]] = []

    def record(request: object, **_: object) -> None:
        headers = {k.lower() for k in request.headers}  # type: ignore[attr-defined]
        sent.append({h for h in headers if h == "content-md5" or h.startswith("x-amz-checksum")})

    storage._client.meta.events.register("before-send.s3.DeleteObjects", record)
    deleted = await storage.delete_prefix(bucket, "corpus/c1/")

    assert deleted == 1006
    assert await _keys(storage, bucket) == set(spared)
    assert len(sent) == 2  # one DeleteObjects per listing page (1000 + 6)
    assert all(sent), f"a DeleteObjects went out without a checksum header: {sent}"


async def test_bounded_cleanup_client_deletes_too(s3_settings) -> None:
    from callobuzz_rag.storage.factory import cleanup_object_storage

    storage = cleanup_object_storage(s3_settings)
    bucket = s3_settings.s3_bucket
    await storage.ensure_bucket(bucket)
    await _put_many(storage, bucket, ["portal/c9/w1/a", "portal/c9/w1/b", "portal/c9/w2/a"])

    assert await storage.delete_prefix(bucket, "portal/c9/w1/") == 2
    assert await storage.delete_prefix(bucket, "portal/c9/w1/") == 0
    assert "portal/c9/w2/a" in await _keys(storage, bucket)


async def test_bounded_client_fails_fast_on_a_dead_endpoint() -> None:
    """Nothing listens there: the bounded client gives up in seconds, not minutes."""
    import time

    from callobuzz_rag.storage.factory import cleanup_object_storage

    settings = Settings(
        _env_file=None,
        storage_provider="s3",
        s3_endpoint="http://127.0.0.1:9",
        s3_access_key="k",
        s3_secret_key="s",
        s3_bucket="b",
    )
    started = time.monotonic()
    with pytest.raises(Exception):  # noqa: B017 - any connection error
        await cleanup_object_storage(settings).delete_prefix("b", "corpus/x/")
    assert time.monotonic() - started < 60
