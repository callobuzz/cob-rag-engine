"""Langfuse tracing integration.

Phase 3 replacement for the Phase 1 no-op stub.

Provides:
- init_langfuse() / shutdown_langfuse() — lifecycle management
- @trace(name) decorator — creates Langfuse spans for sync/async functions
- get_current_trace() / set_current_trace() — contextvars propagation
- TracingContext — manual span creation via context manager
- LangfuseMiddleware — ASGI middleware creating per-request traces

Graceful degradation: if Langfuse is disabled, unreachable, or keys are
missing, all functions are silent no-ops.
"""

import functools
import inspect
import logging
import time
from collections.abc import Callable
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# Module-level Langfuse client (None = disabled / not initialized)
_langfuse_client: Any | None = None

# Contextvars for trace propagation across async calls
_current_trace: ContextVar[Any | None] = ContextVar("_current_trace", default=None)


def init_langfuse(settings: "Settings") -> Any | None:
    """Initialize Langfuse client if enabled and credentials are provided."""
    global _langfuse_client

    if not settings.langfuse_enabled:
        logger.info("Langfuse tracing disabled (RAG_LANGFUSE_ENABLED=false)")
        _langfuse_client = None
        return None

    public_key = settings.langfuse_public_key
    secret_key = settings.langfuse_secret_key.get_secret_value()

    if not public_key or not secret_key:
        logger.warning("Langfuse enabled but public/secret key missing — tracing disabled")
        _langfuse_client = None
        return None

    try:
        from langfuse import Langfuse

        client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=settings.langfuse_host,
        )
        _langfuse_client = client
        logger.info("Langfuse tracing initialized (host=%s)", settings.langfuse_host)
        return client
    except Exception:
        logger.exception("Failed to initialize Langfuse client — tracing disabled")
        _langfuse_client = None
        return None


def shutdown_langfuse() -> None:
    """Flush pending events and shut down the Langfuse client."""
    global _langfuse_client
    if _langfuse_client is not None:
        try:
            _langfuse_client.flush()
            _langfuse_client.shutdown()
        except Exception:
            logger.exception("Error shutting down Langfuse client")
        finally:
            _langfuse_client = None


def get_current_trace() -> Any | None:
    """Get the current Langfuse trace from contextvars."""
    return _current_trace.get()


def set_current_trace(trace_obj: Any) -> Token[Any | None]:
    """Set the current Langfuse trace in contextvars."""
    return _current_trace.set(trace_obj)


def trace(name: str | None = None) -> Callable[[F], F]:
    """Decorator that creates a Langfuse span for the decorated function.

    Supports both sync and async functions. If no Langfuse client is
    initialized or no trace is active, the decorator is a transparent no-op.
    """

    def decorator(func: F) -> F:
        span_name = name or func.__name__

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                current = _current_trace.get()
                if _langfuse_client is None or current is None:
                    return await func(*args, **kwargs)

                span = None
                try:
                    span = current.span(
                        name=span_name,
                        input=_safe_repr(args, kwargs),
                    )
                except Exception:
                    logger.debug("Failed to create Langfuse span", exc_info=True)

                t0 = time.monotonic()
                try:
                    result = await func(*args, **kwargs)
                    if span is not None:
                        elapsed_ms = int((time.monotonic() - t0) * 1000)
                        span.end(
                            output=_safe_repr_value(result),
                            metadata={"latency_ms": elapsed_ms},
                        )
                    return result
                except Exception as exc:
                    if span is not None:
                        elapsed_ms = int((time.monotonic() - t0) * 1000)
                        span.end(
                            output={"error": str(exc)},
                            metadata={"latency_ms": elapsed_ms},
                            level="ERROR",
                        )
                    raise

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            current = _current_trace.get()
            if _langfuse_client is None or current is None:
                return func(*args, **kwargs)

            span = None
            try:
                span = current.span(
                    name=span_name,
                    input=_safe_repr(args, kwargs),
                )
            except Exception:
                logger.debug("Failed to create Langfuse span", exc_info=True)

            t0 = time.monotonic()
            try:
                result = func(*args, **kwargs)
                if span is not None:
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    span.end(
                        output=_safe_repr_value(result),
                        metadata={"latency_ms": elapsed_ms},
                    )
                return result
            except Exception as exc:
                if span is not None:
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    span.end(
                        output={"error": str(exc)},
                        metadata={"latency_ms": elapsed_ms},
                        level="ERROR",
                    )
                raise

        return sync_wrapper  # type: ignore[return-value]

    return decorator


class TracingContext:
    """Context manager for manual span creation.

    Usage:
        with TracingContext("embed_query") as span:
            if span:
                span.update(input={"text": query})
            result = await embedder.embed(query)

    If no client or trace is active, span is None (no-op).
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._span: Any | None = None

    def __enter__(self) -> Any | None:
        current = _current_trace.get()
        if _langfuse_client is None or current is None:
            return None
        try:
            self._span = current.span(name=self._name)
        except Exception:
            logger.debug("Failed to create Langfuse span", exc_info=True)
            self._span = None
        return self._span

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._span is not None:
            try:
                if exc_val is not None:
                    self._span.end(output={"error": str(exc_val)}, level="ERROR")
                else:
                    self._span.end()
            except Exception:
                logger.debug("Failed to end Langfuse span", exc_info=True)


class LangfuseMiddleware:
    """ASGI middleware that creates a Langfuse trace per HTTP request.

    Sets the trace in contextvars so @trace() decorators in the request
    path automatically create child spans.

    Adds X-Trace-Id response header for correlation.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if _langfuse_client is None:
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "UNKNOWN")
        path = scope.get("path", "/")
        trace_obj = None
        token: Token[Any | None] | None = None

        try:
            trace_obj = _langfuse_client.trace(
                name=f"{method} {path}",
                input={"method": method, "path": path},
            )
            token = set_current_trace(trace_obj)
        except Exception:
            logger.debug("Failed to create Langfuse request trace", exc_info=True)

        trace_id = getattr(trace_obj, "id", None)

        async def send_with_trace_header(message: Any) -> None:
            if message["type"] == "http.response.start" and trace_id:
                headers = list(message.get("headers", []))
                headers.append((b"x-trace-id", str(trace_id).encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_trace_header)
        finally:
            if trace_obj is not None:
                import contextlib

                with contextlib.suppress(Exception):
                    trace_obj.update(output={"status": "completed"})
            if token is not None:
                _current_trace.reset(token)


def _safe_repr(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, str]:
    """Safely stringify function arguments for span input."""
    try:
        args_str = str(args)[:500] if args else ""
        kwargs_str = str(kwargs)[:500] if kwargs else ""
        return {"args": args_str, "kwargs": kwargs_str}
    except Exception:
        return {"args": "<unrepresentable>", "kwargs": "<unrepresentable>"}


def _safe_repr_value(value: Any) -> dict[str, str]:
    """Safely stringify a return value for span output."""
    try:
        return {"result": str(value)[:500]}
    except Exception:
        return {"result": "<unrepresentable>"}
