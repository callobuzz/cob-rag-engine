"""Abstract base class for object storage providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO


@dataclass(frozen=True)
class StoredObject:
    """One stored object, as a listing reports it."""

    key: str
    size: int
    last_modified: datetime


class ObjectStorageProvider(ABC):
    """Interface for object storage: a folder on this server (default) or any S3-compatible service."""

    @abstractmethod
    async def upload(
        self, bucket: str, key: str, data: BinaryIO, content_type: str | None = None
    ) -> None:
        """Upload a file-like object to storage."""

    @abstractmethod
    async def download(self, bucket: str, key: str) -> bytes:
        """Download file contents as bytes."""

    @abstractmethod
    async def delete(self, bucket: str, key: str) -> None:
        """Delete a file from storage."""

    @abstractmethod
    async def delete_prefix(self, bucket: str, prefix: str) -> int:
        """Delete every object under a folder prefix ("corpus/{id}/"); return how many."""

    @abstractmethod
    async def list_objects(self, bucket: str, prefix: str = "") -> list[StoredObject]:
        """Every object under `prefix` (the whole bucket when empty), all pages."""

    @abstractmethod
    async def exists(self, bucket: str, key: str) -> bool:
        """Check if a file exists."""

    @abstractmethod
    async def get_presigned_url(self, bucket: str, key: str, expires_in: int = 3600) -> str:
        """Generate a presigned download URL."""

    @abstractmethod
    async def ensure_bucket(self, bucket: str) -> None:
        """Create bucket if it doesn't exist."""
