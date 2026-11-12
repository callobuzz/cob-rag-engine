"""HTTP errors with a machine-readable code.

Every error body the engine adds from now on is
``{"detail": "<human sentence>", "code": "<snake_case code>", ...extra}``. `detail` stays a
string, so every client that reads it (the SDKs, cob-rag-mcp) keeps working; `code` is
what a program branches on. Route code raises `APIError`; middleware, which cannot raise
into FastAPI's handlers, returns `error_response`. A plain `HTTPException` still renders
`{"detail": ...}` only.

`APIError` is an `HTTPException`, not a `RAGKernelError`: it is an HTTP-layer signal,
like every `HTTPException` raised in routes (domain errors inherit `RAGKernelError`).
"""

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse


def error_body(detail: str, code: str, /, **extra: Any) -> dict[str, Any]:
    """The JSON body of a coded error.

    Raises:
        ValueError: `code` is empty, or an extra would shadow `detail` or `code`. A real
            check, not an ``assert``: ``python -O`` strips asserts.
    """
    if not isinstance(code, str) or not code:
        raise ValueError("an error code must be a non-empty string")
    shadowed = sorted({"detail", "code"} & extra.keys())
    if shadowed:
        raise ValueError(f"error extras cannot shadow {', '.join(shadowed)}")
    return {"detail": detail, "code": code, **extra}


class APIError(HTTPException):
    """An HTTP error with a machine-readable `code` next to the human `detail`."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        code: str,
        /,
        headers: dict[str, str] | None = None,
        **extra: Any,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code
        self.extra = extra
        error_body(detail, code, **extra)  # validate the extras up front


def error_response(
    status_code: int,
    detail: str,
    code: str,
    /,
    headers: dict[str, str] | None = None,
    **extra: Any,
) -> JSONResponse:
    """A coded error as a response, for code that cannot raise (middleware)."""
    return JSONResponse(
        status_code=status_code, content=error_body(detail, code, **extra), headers=headers
    )


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render an `APIError`. Starlette picks handlers by the exception's MRO, so this one
    wins over FastAPI's `HTTPException` handler for `APIError` only."""
    if not isinstance(exc, APIError):
        raise exc  # registered for APIError only; anything else is not ours to render
    return error_response(
        exc.status_code,
        str(exc.detail),
        exc.code,
        headers=dict(exc.headers) if exc.headers else None,
        **exc.extra,
    )


async def demo_limit_handler(request: Request, exc: Exception) -> JSONResponse:
    """L5a demo: a demo limit that escapes a route (the model-call backstop on a path with no
    pause reply) is the polite sentence with 429, or 503 when the counters are down, never a 500.
    Raised only on a demo server."""
    from callobuzz_rag.demo import limits

    text = limits.polite_text(exc) or str(exc)
    code = limits.error_code(exc)
    return error_response(503 if code == "demo_unavailable" else 429, text, code)


def register_error_handlers(app: FastAPI) -> None:
    from callobuzz_rag.demo.budget import DemoBudgetExceeded, DemoBudgetUnavailable

    app.add_exception_handler(APIError, api_error_handler)
    app.add_exception_handler(DemoBudgetExceeded, demo_limit_handler)  # L5a demo
    app.add_exception_handler(DemoBudgetUnavailable, demo_limit_handler)  # L5a demo
