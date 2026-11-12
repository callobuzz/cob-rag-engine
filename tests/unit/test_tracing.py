"""Unit tests for Langfuse tracing module."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from callobuzz_rag.core.tracing import (
    TracingContext,
    get_current_trace,
    init_langfuse,
    set_current_trace,
    trace,
)


class TestInitLangfuse:
    """Test Langfuse client initialization."""

    def test_init_when_disabled(self) -> None:
        settings = MagicMock()
        settings.langfuse_enabled = False
        client = init_langfuse(settings)
        assert client is None

    def test_init_when_enabled_but_no_keys(self) -> None:
        settings = MagicMock()
        settings.langfuse_enabled = True
        settings.langfuse_public_key = ""
        settings.langfuse_secret_key = MagicMock()
        settings.langfuse_secret_key.get_secret_value.return_value = ""
        settings.langfuse_host = "http://localhost:3000"
        client = init_langfuse(settings)
        assert client is None

    def test_init_when_enabled_with_keys(self) -> None:
        import sys

        mock_langfuse_cls = MagicMock()
        fake_langfuse_mod = MagicMock()
        fake_langfuse_mod.Langfuse = mock_langfuse_cls
        sys.modules["langfuse"] = fake_langfuse_mod
        try:
            settings = MagicMock()
            settings.langfuse_enabled = True
            settings.langfuse_public_key = "pk-test"
            settings.langfuse_secret_key = MagicMock()
            settings.langfuse_secret_key.get_secret_value.return_value = "sk-test"
            settings.langfuse_host = "http://localhost:3000"
            client = init_langfuse(settings)
            assert client is not None
            mock_langfuse_cls.assert_called_once_with(
                public_key="pk-test",
                secret_key="sk-test",
                host="http://localhost:3000",
            )
        finally:
            del sys.modules["langfuse"]


class TestTraceContextVars:
    """Test contextvars-based trace propagation."""

    def test_set_and_get_trace(self) -> None:
        mock_trace = MagicMock()
        token = set_current_trace(mock_trace)
        assert get_current_trace() is mock_trace
        from callobuzz_rag.core.tracing import _current_trace

        _current_trace.reset(token)

    def test_get_trace_returns_none_by_default(self) -> None:
        assert get_current_trace() is None


class TestTraceDecorator:
    """Test @trace() decorator behavior."""

    def test_sync_function_no_op_when_no_client(self) -> None:
        @trace("test_sync")
        def my_func(x: int, y: int) -> int:
            return x + y

        result = my_func(3, 4)
        assert result == 7

    def test_async_function_no_op_when_no_client(self) -> None:
        @trace("test_async")
        async def my_func(x: int) -> int:
            return x * 2

        result = asyncio.run(my_func(5))
        assert result == 10

    @patch("callobuzz_rag.core.tracing._langfuse_client")
    def test_sync_function_creates_span(self, mock_client: MagicMock) -> None:
        mock_trace = MagicMock()
        mock_span = MagicMock()
        mock_trace.span.return_value = mock_span

        token = set_current_trace(mock_trace)
        try:

            @trace("my_step")
            def my_func(val: str) -> str:
                return val.upper()

            result = my_func("hello")
            assert result == "HELLO"
            mock_trace.span.assert_called_once()
            call_kwargs = mock_trace.span.call_args[1]
            assert call_kwargs["name"] == "my_step"
            mock_span.end.assert_called_once()
        finally:
            from callobuzz_rag.core.tracing import _current_trace

            _current_trace.reset(token)

    @patch("callobuzz_rag.core.tracing._langfuse_client")
    def test_async_function_creates_span(self, mock_client: MagicMock) -> None:
        mock_trace = MagicMock()
        mock_span = MagicMock()
        mock_trace.span.return_value = mock_span

        token = set_current_trace(mock_trace)
        try:

            @trace("async_step")
            async def my_func(val: int) -> int:
                return val + 1

            result = asyncio.run(my_func(10))
            assert result == 11
            mock_trace.span.assert_called_once()
            mock_span.end.assert_called_once()
        finally:
            from callobuzz_rag.core.tracing import _current_trace

            _current_trace.reset(token)

    def test_decorator_preserves_function_name(self) -> None:
        @trace("named")
        def original_name() -> None:
            pass

        assert original_name.__name__ == "original_name"

    def test_decorator_preserves_async_function_name(self) -> None:
        @trace("named")
        async def original_async() -> None:
            pass

        assert original_async.__name__ == "original_async"

    def test_exception_propagates(self) -> None:
        @trace("failing")
        def bad_func() -> None:
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            bad_func()

    def test_async_exception_propagates(self) -> None:
        @trace("failing_async")
        async def bad_func() -> None:
            raise ValueError("async boom")

        with pytest.raises(ValueError, match="async boom"):
            asyncio.run(bad_func())


class TestTracingContext:
    """Test TracingContext context manager."""

    def test_no_op_when_no_client(self) -> None:
        with TracingContext("test") as span:
            assert span is None

    @patch("callobuzz_rag.core.tracing._langfuse_client")
    def test_creates_span_when_trace_active(self, mock_client: MagicMock) -> None:
        mock_trace = MagicMock()
        mock_span = MagicMock()
        mock_trace.span.return_value = mock_span

        token = set_current_trace(mock_trace)
        try:
            with TracingContext("manual_span") as span:
                assert span is mock_span
            mock_span.end.assert_called_once()
        finally:
            from callobuzz_rag.core.tracing import _current_trace

            _current_trace.reset(token)


class TestLangfuseMiddleware:
    """Test the FastAPI middleware for trace-per-request."""

    @pytest.fixture
    def _reset_client(self) -> None:
        import callobuzz_rag.core.tracing as tracing_mod

        original = tracing_mod._langfuse_client
        tracing_mod._langfuse_client = None
        yield
        tracing_mod._langfuse_client = original

    def test_middleware_no_op_when_disabled(self, _reset_client: None) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from callobuzz_rag.core.tracing import LangfuseMiddleware

        app = FastAPI()
        app.add_middleware(LangfuseMiddleware)

        @app.get("/test")
        async def test_route() -> dict[str, bool]:
            return {"ok": True}

        client = TestClient(app)
        resp = client.get("/test")
        assert resp.status_code == 200
        assert "X-Trace-Id" not in resp.headers

    @patch("callobuzz_rag.core.tracing._langfuse_client")
    def test_middleware_sets_trace_header_when_enabled(self, mock_client: MagicMock) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from callobuzz_rag.core.tracing import LangfuseMiddleware

        mock_trace = MagicMock()
        mock_trace.id = "trace-abc-123"
        mock_client.trace.return_value = mock_trace

        app = FastAPI()
        app.add_middleware(LangfuseMiddleware)

        @app.get("/test")
        async def test_route() -> dict[str, bool]:
            return {"ok": True}

        client = TestClient(app)
        resp = client.get("/test")
        assert resp.status_code == 200
        assert resp.headers.get("X-Trace-Id") == "trace-abc-123"
