"""S3-compatible implementation of ObjectStorageProvider.

Works against any S3 API: AWS S3, Cloudflare R2, Supabase Storage, Backblaze B2,
Vultr, DigitalOcean Spaces, or an S3 server you run yourself (MinIO is one such
server; it is not shipped with the engine). Switching provider is four environment
variables — endpoint, access key, secret key, region — plus `RAG_S3_ADDRESSING_STYLE`
for the handful of gateways that only serve one style. Used when
`RAG_STORAGE_PROVIDER=s3`; build it through `storage.factory.object_storage`.

Uses boto3 synchronous client wrapped in asyncio.to_thread() for non-blocking I/O.
boto3 does not have native async support — this is the standard pattern.
"""

import asyncio
import io
import logging
from typing import Any, BinaryIO

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.exceptions import StorageError
from callobuzz_rag.storage.base import ObjectStorageProvider, StoredObject

logger = logging.getLogger(__name__)


class S3ObjectStorageProvider(ObjectStorageProvider):
    def __init__(
        self,
        settings: Settings,
        *,
        connect_timeout: float | None = None,
        read_timeout: float | None = None,
        max_attempts: int | None = None,
    ) -> None:
        """`connect_timeout`, `read_timeout` (seconds) and `max_attempts` bound every call;
        left out, botocore's defaults apply (60 s each, legacy retries). Background cleanup
        passes small values so a dead endpoint fails fast instead of holding a worker."""
        access_key = settings.s3_access_key.get_secret_value()
        secret_key = settings.s3_secret_key.get_secret_value()
        if not access_key or not secret_key:
            raise StorageError(
                "S3 storage needs RAG_S3_ACCESS_KEY and RAG_S3_SECRET_KEY (or set "
                "RAG_STORAGE_PROVIDER=local to keep files on this server)"
            )
        self._region = settings.s3_region
        bounds: dict[str, Any] = {}
        if connect_timeout is not None:
            bounds["connect_timeout"] = connect_timeout
        if read_timeout is not None:
            bounds["read_timeout"] = read_timeout
        if max_attempts is not None:
            bounds["retries"] = {"max_attempts": max_attempts, "mode": "standard"}
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint or None,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=settings.s3_region,
            config=BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": settings.s3_addressing_style},
                # botocore >= 1.36 defaults BOTH of these to "when_supported",
                # which attaches a CRC32 checksum to every PutObject and sends
                # the body as `aws-chunked`. Real AWS accepts it; a long tail of
                # S3-compatible gateways does not, and the failure is an opaque
                # XAmzContentSHA256Mismatch or 501 rather than anything naming a
                # checksum. "when_required" restores the pre-1.36 behaviour:
                # checksums are still sent where the API mandates them, and
                # nowhere else. This is what makes "point it at any S3" true.
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
                **bounds,
            ),
        )

    async def upload(
        self, bucket: str, key: str, data: BinaryIO, content_type: str | None = None
    ) -> None:
        logger.info("Uploading to %s/%s (content_type=%s)", bucket, key, content_type)
        extra_args: dict[str, str] = {}
        if content_type:
            extra_args["ContentType"] = content_type
        await asyncio.to_thread(
            self._client.upload_fileobj, data, bucket, key, ExtraArgs=extra_args or None
        )

    async def download(self, bucket: str, key: str) -> bytes:
        logger.info("Downloading %s/%s", bucket, key)
        buffer = io.BytesIO()
        await asyncio.to_thread(self._client.download_fileobj, bucket, key, buffer)
        buffer.seek(0)
        return buffer.read()

    async def delete(self, bucket: str, key: str) -> None:
        logger.info("Deleting %s/%s", bucket, key)
        await asyncio.to_thread(self._client.delete_object, Bucket=bucket, Key=key)

    async def delete_prefix(self, bucket: str, prefix: str) -> int:
        """Delete every object under `prefix`, a folder such as ``corpus/{id}/``.

        Lists page by page and deletes each page in one DeleteObjects call (at most
        1000 keys, the API's limit). Only a folder prefix is accepted: an empty or
        bare one could match the whole bucket.

        Raises:
            ValueError: `prefix` is not a non-empty folder prefix ending in "/".
            RuntimeError: S3 refused to delete some objects.
        """
        if not prefix.endswith("/") or not prefix.strip("/"):
            raise ValueError(
                f"delete_prefix needs a folder prefix like 'corpus/<id>/', got {prefix!r}"
            )

        def _run() -> int:
            deleted = 0
            failed: list[str] = []
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
                if not keys:
                    continue
                result = self._client.delete_objects(
                    Bucket=bucket, Delete={"Objects": keys, "Quiet": True}
                )
                errors = result.get("Errors", []) if isinstance(result, dict) else []
                failed += [e.get("Key", "?") for e in errors]
                deleted += len(keys) - len(errors)
            if failed:
                raise RuntimeError(
                    f"S3 refused to delete {len(failed)} object(s) under {bucket}/{prefix}"
                )
            return deleted

        count = await asyncio.to_thread(_run)
        logger.info("Deleted %d objects under %s/%s", count, bucket, prefix)
        return count

    async def list_objects(self, bucket: str, prefix: str = "") -> list[StoredObject]:
        def _run() -> list[StoredObject]:
            paginator = self._client.get_paginator("list_objects_v2")
            return [
                StoredObject(key=o["Key"], size=int(o["Size"]), last_modified=o["LastModified"])
                for page in paginator.paginate(Bucket=bucket, Prefix=prefix)
                for o in page.get("Contents", [])
            ]

        return await asyncio.to_thread(_run)

    async def exists(self, bucket: str, key: str) -> bool:
        logger.debug("Checking existence of %s/%s", bucket, key)
        try:
            await asyncio.to_thread(self._client.head_object, Bucket=bucket, Key=key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            raise

    async def get_presigned_url(self, bucket: str, key: str, expires_in: int = 3600) -> str:
        return await asyncio.to_thread(
            self._client.generate_presigned_url,
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    async def bucket_exists(self, bucket: str) -> bool:
        """Whether the bucket is there and this credential may use it.

        A 403 counts as True: the bucket exists, the credential simply may not
        list it, which is normal for a key scoped to object operations only.
        """
        try:
            await asyncio.to_thread(self._client.head_bucket, Bucket=bucket)
            return True
        except ClientError as e:
            code = str(e.response.get("Error", {}).get("Code", ""))
            if code in ("403", "AccessDenied"):
                return True
            if code in ("404", "NoSuchBucket"):
                return False
            raise

    async def ensure_bucket(self, bucket: str) -> None:
        """Create the bucket if it is missing, tolerating a read-only credential.

        HEAD first, create second. Going straight to CreateBucket — which is what
        this used to do — demands the CreateBucket permission on every call, so a
        credential correctly scoped to one existing bucket fails here even though
        the bucket is right there. A 403 on the HEAD means the bucket exists and
        we may not inspect it, which is a perfectly good outcome: creating it
        would fail too, and uploads will still work.
        """
        try:
            await asyncio.to_thread(self._client.head_bucket, Bucket=bucket)
            logger.debug("Bucket %s already exists", bucket)
            return
        except ClientError as e:
            code = str(e.response.get("Error", {}).get("Code", ""))
            if code in ("403", "AccessDenied"):
                logger.info(
                    "Bucket %s is not inspectable with this credential (403) — assuming it "
                    "exists and is writable.",
                    bucket,
                )
                return
            if code not in ("404", "NoSuchBucket"):
                raise

        logger.info("Bucket %s missing — creating it", bucket)
        kwargs: dict[str, Any] = {"Bucket": bucket}
        # LocationConstraint is an AWS-ism, and AWS rejects it for us-east-1
        # while requiring it everywhere else. R2 uses the literal "auto", which
        # is not a location constraint at all — sending it produces
        # InvalidLocationConstraint. MinIO ignores the whole field.
        if self._region and self._region not in ("us-east-1", "auto"):
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": self._region}
        try:
            await asyncio.to_thread(self._client.create_bucket, **kwargs)
        except ClientError as e:
            # Lost a race with another worker starting at the same moment.
            if e.response["Error"]["Code"] not in (
                "BucketAlreadyOwnedByYou",
                "BucketAlreadyExists",
            ):
                raise
