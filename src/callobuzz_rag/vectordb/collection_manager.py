"""Qdrant collection lifecycle management.

Ties collection create/delete to corpus lifecycle. Provides:
- create_for_corpus: Create a Qdrant collection for a new corpus
- delete_for_corpus: Delete the collection when a corpus is removed
- ensure_exists: Idempotent check-and-create for self-healing
"""

import logging
import re

from callobuzz_rag.core.exceptions import VectorStoreError
from callobuzz_rag.vectordb.base import VectorStorageProvider

logger = logging.getLogger(__name__)


class CollectionManager:
    """Manages Qdrant collection lifecycle for corpora."""

    _COLLECTION_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

    def __init__(
        self,
        vector_store: VectorStorageProvider,
        embedding_dimensions: int,
    ) -> None:
        self._vector_store = vector_store
        self._embedding_dimensions = embedding_dimensions

    def _validate_collection_name(self, collection_name: str) -> None:
        """Validate that the collection name is safe for Qdrant."""
        if not collection_name or not collection_name.strip():
            raise VectorStoreError("Collection name cannot be empty")
        if len(collection_name) > 128:
            raise VectorStoreError("Collection name too long (max 128 chars)")
        if not self._COLLECTION_NAME_RE.match(collection_name):
            raise VectorStoreError("Collection name contains invalid characters")

    async def create_for_corpus(self, collection_name: str) -> None:
        """Create a Qdrant collection for a corpus. Idempotent."""
        self._validate_collection_name(collection_name)
        logger.info(
            "Creating Qdrant collection for corpus: %s (dimensions=%d)",
            collection_name,
            self._embedding_dimensions,
        )
        await self._vector_store.create_collection(collection_name, self._embedding_dimensions)

    async def delete_for_corpus(self, collection_name: str) -> None:
        """Delete the Qdrant collection for a corpus."""
        self._validate_collection_name(collection_name)
        logger.info("Deleting Qdrant collection for corpus: %s", collection_name)
        await self._vector_store.delete_collection(collection_name)

    async def ensure_exists(self, collection_name: str) -> None:
        """Ensure a Qdrant collection exists, creating it if missing. Idempotent."""
        self._validate_collection_name(collection_name)
        logger.debug("Ensuring Qdrant collection exists: %s", collection_name)
        await self._vector_store.create_collection(collection_name, self._embedding_dimensions)
