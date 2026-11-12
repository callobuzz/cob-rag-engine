"""Tests for content_portal feature flag gating + exception hierarchy."""

from unittest.mock import MagicMock, patch

from callobuzz_rag.core.exceptions import (
    ContentPortalError,
    PortalDisabledError,
    RAGKernelError,
    WorkspaceLockedError,
    WorkspaceStateError,
)

# ---------- Exception hierarchy ----------


class TestExceptionHierarchy:
    def test_content_portal_error_is_rag_kernel_error(self) -> None:
        assert issubclass(ContentPortalError, RAGKernelError)

    def test_workspace_locked_is_portal_error(self) -> None:
        assert issubclass(WorkspaceLockedError, ContentPortalError)

    def test_workspace_state_is_portal_error(self) -> None:
        assert issubclass(WorkspaceStateError, ContentPortalError)

    def test_portal_disabled_is_portal_error(self) -> None:
        assert issubclass(PortalDisabledError, ContentPortalError)


# ---------- get_portal_router ----------


class TestGetPortalRouter:
    def test_returns_none_when_disabled(self) -> None:
        mock_settings = MagicMock()
        mock_settings.enable_content_portal = False

        with patch(
            "callobuzz_rag.core.config.get_settings",
            return_value=mock_settings,
        ):
            from callobuzz_rag.content_portal import get_portal_router

            result = get_portal_router()
            assert result is None

    def test_returns_router_when_enabled(self) -> None:
        from fastapi import APIRouter

        mock_settings = MagicMock()
        mock_settings.enable_content_portal = True

        with patch(
            "callobuzz_rag.core.config.get_settings",
            return_value=mock_settings,
        ):
            from callobuzz_rag.content_portal import get_portal_router

            result = get_portal_router()
            assert isinstance(result, APIRouter)

    def test_router_has_portal_prefix(self) -> None:
        mock_settings = MagicMock()
        mock_settings.enable_content_portal = True

        with patch(
            "callobuzz_rag.core.config.get_settings",
            return_value=mock_settings,
        ):
            from callobuzz_rag.content_portal import get_portal_router

            router = get_portal_router()
            assert router is not None
            assert router.prefix == "/api/v1/portal"
