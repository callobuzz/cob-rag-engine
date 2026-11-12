"""Health checks: liveness (`/health`) and readiness (`/health/ready`).

Both ping the same services. `/health` always answers 200 with a `status` of `ok` or
`degraded`, so a container healthcheck or a dashboard can read the detail without the
process being restarted for a slow dependency. `/health/ready` answers 503 when a
service the engine cannot work without (Postgres, Redis or Qdrant) is down, which is
what a load balancer, an uptime monitor or a deploy script needs from a status code.
Object storage is reported by `/health` only: the engine still answers queries without it.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request, Response

from callobuzz_rag.api.schemas.health import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

# The services without which the engine cannot serve a request.
REQUIRED_SERVICES: tuple[str, ...] = ("postgres", "redis", "qdrant")

# A dependency that is down by dropped packets would otherwise hold a poll open for the
# OS connect timeout. Every probe is cut off here and counts as down.
PROBE_TIMEOUT_S = 3.0
_SOCKET_TIMEOUT_S = 2


async def _check_postgres(request: Request) -> bool:
    try:
        from sqlalchemy import text

        session_factory = request.app.state.session_factory
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.warning("PostgreSQL health check failed: %s", e)
        return False


async def _check_redis(request: Request) -> bool:
    client = None
    try:
        import redis.asyncio as aioredis

        settings = request.app.state.settings
        # from_url, not host/port: the URL is what carries the password, and a
        # health check that connects differently from the application is not
        # checking the application's connection. Async, so a stalled socket never
        # blocks the event loop; the timeouts match WidgetRateLimiter.
        client = aioredis.from_url(  # type: ignore[no-untyped-call]
            settings.redis_url,
            socket_timeout=_SOCKET_TIMEOUT_S,
            socket_connect_timeout=_SOCKET_TIMEOUT_S,
        )
        await client.ping()
        return True
    except Exception as e:
        logger.warning("Redis health check failed: %s", e)
        return False
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                logger.debug("Redis health client did not close cleanly", exc_info=True)


async def _check_qdrant(request: Request) -> bool:
    client = None
    try:
        from qdrant_client import AsyncQdrantClient

        settings = request.app.state.settings
        client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key_or_none,
            timeout=_SOCKET_TIMEOUT_S,
        )
        await client.get_collections()
        return True
    except Exception as e:
        logger.warning("Qdrant health check failed: %s", e)
        return False
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                logger.debug("Qdrant health client did not close cleanly", exc_info=True)


async def _check_object_storage(request: Request) -> bool:
    # File storage (a folder on this server, or any S3-compatible store; reported as
    # "storage"). Checks the configured BUCKET, not just the endpoint: listing buckets
    # passed while the bucket was missing and every upload failed.
    try:
        from callobuzz_rag.storage.factory import object_storage, storage_reachable

        settings = request.app.state.settings
        storage = object_storage(settings)
        present = await storage_reachable(storage, settings.s3_bucket)
        if not present:
            logger.warning("Object storage bucket %r is missing", settings.s3_bucket)
        return present
    except Exception as e:
        logger.warning("Object storage health check failed: %s", e)
        return False


async def _bounded(
    name: str, probe: Callable[[Request], Awaitable[bool]], request: Request
) -> bool:
    """Run one probe under `PROBE_TIMEOUT_S`; a hang or an escaped error is "down"."""
    try:
        return await asyncio.wait_for(probe(request), PROBE_TIMEOUT_S)
    except TimeoutError:
        logger.warning("%s health check timed out after %.1fs", name, PROBE_TIMEOUT_S)
        return False
    except Exception as e:
        logger.warning("%s health check failed: %s", name, e)
        return False


async def _collect(request: Request, *, include_storage: bool = True) -> dict[str, bool]:
    """Probe the dependencies concurrently, so the slowest one sets the latency."""
    probes: dict[str, Callable[[Request], Awaitable[bool]]] = {
        "postgres": _check_postgres,
        "redis": _check_redis,
        "qdrant": _check_qdrant,
    }
    if include_storage:
        probes["storage"] = _check_object_storage
    results = await asyncio.gather(*(_bounded(n, p, request) for n, p in probes.items()))
    return {"api": True, **dict(zip(probes, results, strict=True))}


@router.get("/api/v1/health", response_model=HealthResponse)
async def health_check(request: Request) -> HealthResponse:
    """Liveness: connectivity to all dependent services. Always HTTP 200."""
    services = await _collect(request)
    return HealthResponse(status="ok" if all(services.values()) else "degraded", services=services)


@router.get(
    "/api/v1/health/ready",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse, "description": "A required service is down."}},
)
async def readiness_check(request: Request, response: Response) -> HealthResponse:
    """Readiness: HTTP 503 when Postgres, Redis or Qdrant is down, 200 otherwise.

    `status` is `ready` when every required service answers, `not_ready` when one does
    not. Object storage is neither probed nor listed: it never decides readiness, and an
    unauthenticated poll should not cost an S3 round trip.
    """
    services = await _collect(request, include_storage=False)
    ready = all(services[name] for name in REQUIRED_SERVICES)
    if not ready:
        response.status_code = 503
    return HealthResponse(status="ready" if ready else "not_ready", services=services)
