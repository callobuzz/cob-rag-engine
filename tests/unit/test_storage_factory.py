from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import SecretStr, ValidationError

from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.exceptions import StorageError
from callobuzz_rag.storage.factory import (
    cleanup_object_storage,
    local_root,
    object_storage,
    storage_reachable,
)
from callobuzz_rag.storage.local import LocalFileStorageProvider
from callobuzz_rag.storage.s3 import S3ObjectStorageProvider


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def _s3_settings() -> Settings:
    return _settings(
        storage_provider="s3", s3_access_key=SecretStr("k"), s3_secret_key=SecretStr("s")
    )


def test_local_is_the_default_and_uses_the_configured_root(tmp_path: Path) -> None:
    settings = _settings(storage_local_root=str(tmp_path / "f"))
    assert settings.storage_provider == "local"
    storage = object_storage(settings)
    assert isinstance(storage, LocalFileStorageProvider)
    assert (tmp_path / "f").is_dir()


def test_a_relative_root_is_anchored_to_the_project_not_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    root = local_root(_settings())
    assert root.is_absolute()
    assert (root.parent.parent / "pyproject.toml").is_file()
    assert root.name == "files" and root.parent.name == "data"


def test_s3_builds_the_s3_provider() -> None:
    assert isinstance(object_storage(_s3_settings()), S3ObjectStorageProvider)


def test_s3_refuses_empty_keys_with_a_plain_error() -> None:
    with pytest.raises(StorageError, match="RAG_S3_ACCESS_KEY"):
        object_storage(_settings(storage_provider="s3"))


def test_cleanup_storage_is_bounded_for_s3() -> None:
    from callobuzz_rag.storage.cleanup import (
        CLEANUP_CONNECT_TIMEOUT,
        CLEANUP_MAX_ATTEMPTS,
        CLEANUP_READ_TIMEOUT,
    )

    with patch("callobuzz_rag.storage.s3.boto3") as boto:
        cleanup_object_storage(_s3_settings())
    config = boto.client.call_args.kwargs["config"]
    assert config.connect_timeout == CLEANUP_CONNECT_TIMEOUT
    assert config.read_timeout == CLEANUP_READ_TIMEOUT
    assert config.retries == {"max_attempts": CLEANUP_MAX_ATTEMPTS, "mode": "standard"}


def test_cleanup_storage_is_the_local_store_for_local(tmp_path: Path) -> None:
    storage = cleanup_object_storage(_settings(storage_local_root=str(tmp_path)))
    assert isinstance(storage, LocalFileStorageProvider)


def test_unknown_provider_is_refused() -> None:
    with pytest.raises(ValidationError):
        _settings(storage_provider="minio")


async def test_reachable_for_a_writable_folder(tmp_path: Path) -> None:
    storage = LocalFileStorageProvider(tmp_path / "f")
    assert await storage_reachable(storage, "rag-documents") is True


async def test_not_reachable_when_the_root_is_a_file(tmp_path: Path) -> None:
    blocker = tmp_path / "f"
    blocker.write_text("x")
    assert await storage_reachable(LocalFileStorageProvider(blocker), "rag-documents") is False
