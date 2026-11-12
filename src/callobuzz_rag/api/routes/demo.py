"""Demo mode status and reset, for the deployer (L5a).

`GET /api/v1/admin/demo` says when the demo was last put back to the seed, when it next is, and
how many model calls it has used today. `POST /api/v1/admin/demo/reset` starts a reset now (the
nightly one runs by itself). Both need the admin key and answer 404 `demo_mode_off` on a server
that is not a demo. The reset runs as a background task of this process; the demo guard refuses
this route for a signed-in super admin, like every `manage` route, so only the admin key (which
the deployer holds and the demo never shows) can use it.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, Request

from callobuzz_rag.api.deps import require_admin
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.demo import DemoStatusOut
from callobuzz_rag.core.exceptions import RAGKernelError
from callobuzz_rag.demo import runtime

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/demo", tags=["demo"], dependencies=[Depends(require_admin)]
)


def _demo_only(request: Request) -> None:
    if not request.app.state.settings.demo_mode:
        raise APIError(404, "Demo mode is off on this server.", "demo_mode_off")


def _unavailable() -> APIError:
    return APIError(
        503,
        "The demo reset cannot start right now because the queue server did not answer. "
        "Try again shortly.",
        "demo_unavailable",
    )


@router.get("", response_model=DemoStatusOut)
async def get_demo(request: Request) -> DemoStatusOut:
    """The demo's status: last and next reset, and today's model calls."""
    _demo_only(request)
    try:
        return await runtime.read_status(request.app)
    except runtime.DemoUnavailableError as exc:
        raise _unavailable() from exc


async def _reset_in_background(app: object) -> None:
    try:
        report = await runtime.run_for_app(app, "manual")
        logger.info("demo: manual reset finished (%s)", report.result)
    except RAGKernelError as exc:
        logger.warning("demo: manual reset did not run (%s)", type(exc).__name__)
    except Exception:
        logger.exception("demo: manual reset crashed")


@router.post("/reset", response_model=DemoStatusOut, status_code=202)
async def reset_demo(request: Request) -> DemoStatusOut:
    """Start a reset now. It runs in the background; read `GET` to follow it."""
    _demo_only(request)
    try:
        status = await runtime.read_status(request.app)
    except runtime.DemoUnavailableError as exc:
        raise _unavailable() from exc
    if status.running:
        raise APIError(
            409,
            "A demo reset is already running. Try again in a few minutes.",
            "demo_reset_running",
        )
    # Held on app.state: a bare task may be garbage collected before it finishes.
    request.app.state.demo_reset_task = asyncio.create_task(_reset_in_background(request.app))
    return status.model_copy(update={"running": True})
