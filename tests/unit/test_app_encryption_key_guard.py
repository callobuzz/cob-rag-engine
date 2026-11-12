"""The startup guard on RAG_ENCRYPTION_KEY (plan S8).

Before this guard, a blank key made the app generate a throwaway one on every
restart and carry on, logging a warning. Anything the previous run encrypted
became unreadable, silently. On a production box that is a data-destroying
default, so outside debug the process must now refuse to start.

These exercise the real `lifespan` context manager with real environment
variables — `get_settings()` constructs `Settings()` fresh from the environment
on every call, so nothing needs to be faked. The guard runs before the database
engine, Qdrant client or Langfuse client are constructed, which is both the
correct ordering (abort before acquiring anything) and what makes these tests
runnable without any infrastructure.
"""

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI

from callobuzz_rag.api.app import lifespan


def _key() -> str:
    return Fernet.generate_key().decode()


class TestEncryptionKeyStartupGuard:
    async def test_blank_key_outside_debug_refuses_to_start(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RAG_ENCRYPTION_KEY", "")
        monkeypatch.setenv("RAG_DEBUG", "false")

        with pytest.raises(RuntimeError, match="RAG_ENCRYPTION_KEY is not set"):
            async with lifespan(FastAPI()):
                pass  # pragma: no cover — the guard must abort before this

    async def test_the_error_tells_you_how_to_fix_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An abort message that does not say what to do just moves the problem
        to 3am on someone else's shift."""
        monkeypatch.setenv("RAG_ENCRYPTION_KEY", "")
        monkeypatch.setenv("RAG_DEBUG", "false")

        with pytest.raises(RuntimeError) as excinfo:
            async with lifespan(FastAPI()):
                pass  # pragma: no cover

        message = str(excinfo.value)
        assert "Fernet.generate_key()" in message
        assert "write-only" in message  # keep your own copy — GitHub cannot show it back
        assert "comma-separated" in message  # the rotation path

    async def test_an_invalid_key_is_rejected_at_startup_not_first_use(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A malformed key must fail the boot, not the first customer request
        that happens to touch an encrypted field."""
        monkeypatch.setenv("RAG_ENCRYPTION_KEY", "obviously-not-a-fernet-key")
        monkeypatch.setenv("RAG_DEBUG", "false")

        from callobuzz_rag.core.secrets import EncryptionError

        with pytest.raises(EncryptionError):
            async with lifespan(FastAPI()):
                pass  # pragma: no cover

    async def test_a_stale_key_in_a_rotation_list_is_caught_at_startup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Half-finished rotations are a real way to configure this wrongly:
        one good key plus one typo'd trailing key must not boot."""
        monkeypatch.setenv("RAG_ENCRYPTION_KEY", f"{_key()},not-a-key")
        monkeypatch.setenv("RAG_DEBUG", "false")

        from callobuzz_rag.core.secrets import EncryptionError

        with pytest.raises(EncryptionError):
            async with lifespan(FastAPI()):
                pass  # pragma: no cover
