"""The app keeps its object storage on app.state, so deleting a corpus can clean files.

`delete_corpus` removes `corpus/{id}/` and `portal/{id}/` through
`app.state.object_store`, which the lifespan never set (and the provider had no
`delete_prefix` either): every deleted corpus left its uploads, and its Content
Portal staged files, in the bucket.
"""

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

from callobuzz_rag.api import app as app_module
from callobuzz_rag.core.config import Settings


def _settings(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[arg-type]


async def test_returns_the_provider_when_the_bucket_is_reachable() -> None:
    storage = MagicMock(bucket_exists=AsyncMock(return_value=True))
    with patch("callobuzz_rag.storage.factory.object_storage", return_value=storage):
        assert (
            await app_module._object_storage(
                _settings(storage_provider="s3", s3_auto_create_bucket=False)
            )
            is storage
        )


async def test_a_failed_check_still_returns_the_provider() -> None:
    """The endpoint may come back; cleanup must not be switched off for the whole run."""
    storage = MagicMock(ensure_bucket=AsyncMock(side_effect=RuntimeError("s3 down")))
    with patch("callobuzz_rag.storage.factory.object_storage", return_value=storage):
        assert (
            await app_module._object_storage(
                _settings(storage_provider="s3", s3_auto_create_bucket=True)
            )
            is storage
        )


async def test_none_when_it_cannot_be_built() -> None:
    with patch(
        "callobuzz_rag.storage.factory.object_storage", side_effect=ValueError("bad endpoint")
    ):
        assert await app_module._object_storage(_settings(storage_provider="s3")) is None


def test_lifespan_puts_it_on_app_state() -> None:
    source = inspect.getsource(app_module.lifespan)
    assert "app.state.object_store = await _object_storage(settings)" in source
