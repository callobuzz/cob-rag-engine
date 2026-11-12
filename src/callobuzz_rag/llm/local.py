"""The free local model: a small open model on this server's CPU (PIP2).

The optional `local-llm` compose service runs Ollama. This module answers three questions
without ever slowing an answer down:

- *Is it ready?* `local_status` asks the server (`GET /api/tags`) and caches the result;
  `local_ready` reads only that cache, so the answer path never does I/O. A lifespan task
  (`start_refresher`) keeps the cache fresh, every 15 s while ready and every 5 s otherwise.
  A cache nobody refreshed for a minute counts as not ready.
- *Which models does it hold?* `local_models` (the same cache). A profile's language map may only
  name models the server lists: nothing is ever downloaded in the answer path.
- *What does a call look like?* `local_route` is the LiteLLM route the pipeline overlay plugs in:
  provider `ollama_chat`, the server's address, a placeholder key that is not a secret, and
  nothing else. The engine's real key never travels to the container.

When the model first turns ready, one empty `/api/generate` loads it into memory so the first
visitor does not wait for a cold start. A model the server lists but cannot load (too little
memory, a damaged file) is *not* ready: the failed load turns the state to `down` and is retried
at every refresh. A status poll (`warm=False`) never loads the model, and never hides that the
load is still to do: the refresher warms any ready, not yet warmed model.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

LocalState = Literal["not_configured", "starting", "pulling", "ready", "down"]

PROVIDER = "ollama_chat"
PLACEHOLDER_KEY = "local"  # not a secret: it only makes the provider count as "has a key"
READY_TTL_S = 15.0
NOT_READY_TTL_S = 5.0
LOAD_FAILED_TTL_S = 30.0  # a load that failed is not retried every few seconds
STALE_AFTER_S = 60.0
PROBE_TIMEOUT_S = 2.0


@dataclass(frozen=True, slots=True)
class LocalStatus:
    enabled: bool
    state: LocalState
    model: str
    models: tuple[str, ...]
    checked_at: datetime


@dataclass(frozen=True, slots=True)
class LocalRoute:
    provider: str
    model: str
    base_url: str
    api_key: str
    timeout: float


@dataclass(slots=True)
class _Cache:
    state: LocalState = "starting"
    models: tuple[str, ...] = ()
    at_monotonic: float = 0.0
    checked_at: datetime | None = None
    url: str = ""
    model: str = ""
    warmed: bool = False  # the model was loaded into memory (an empty generate succeeded)
    warm_failed: bool = False  # the last load attempt failed: listed, but it cannot answer


_CACHE = _Cache()
_STARTED = time.monotonic()
_EVER_UP = False
_WARM_LOCK = asyncio.Lock()
STARTING_GRACE_S = 15 * 60.0


def reset_cache() -> None:
    """Forget what was last seen (tests)."""
    global _CACHE, _EVER_UP, _STARTED
    _CACHE = _Cache()
    _EVER_UP = False
    _STARTED = time.monotonic()


def safe_address(url: str) -> str:
    """`host:port` of a server address, never credentials or a path (for logs and the API)."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    return f"{host}:{parts.port}" if parts.port else host


def same_model(listed: str, wanted: str) -> bool:
    """Ollama lists `gemma3:4b`; a bare `gemma3` means its `latest` tag."""
    if listed == wanted:
        return True
    return ":" not in wanted and listed == f"{wanted}:latest"


def _enabled(settings: Any) -> bool:
    return bool(getattr(settings, "local_llm_enabled", False))


def _fresh(settings: Any) -> bool:
    """Whether the cache describes this install and is recent enough to trust."""
    return (
        _CACHE.at_monotonic > 0
        and _CACHE.url == settings.local_llm_url
        and _CACHE.model == settings.local_llm_model
        and time.monotonic() - _CACHE.at_monotonic < STALE_AFTER_S
    )


def local_ready(settings: Any) -> bool:
    """Whether the local model can answer right now. Reads the cache only: no I/O, never raises."""
    return _enabled(settings) and _fresh(settings) and _CACHE.state == "ready"


def local_models(settings: Any) -> tuple[str, ...]:
    """The models the server listed at the last check (empty when unknown)."""
    return _CACHE.models if _enabled(settings) and _fresh(settings) else ()


def local_route(settings: Any, model: str | None = None) -> LocalRoute:
    """The LiteLLM route to the local model."""
    return LocalRoute(
        provider=PROVIDER,
        model=model or settings.local_llm_model,
        base_url=settings.local_llm_url,
        api_key=PLACEHOLDER_KEY,
        timeout=float(settings.local_llm_timeout_seconds),
    )


async def _probe(
    settings: Any, transport: httpx.AsyncBaseTransport | None
) -> tuple[LocalState, tuple[str, ...]]:
    global _EVER_UP
    try:
        async with httpx.AsyncClient(
            base_url=settings.local_llm_url, timeout=PROBE_TIMEOUT_S, transport=transport
        ) as client:
            resp = await client.get("/api/tags")
            resp.raise_for_status()
            body = resp.json()
    except (httpx.HTTPError, ValueError):
        # A server that has never answered since this process began, and the process is young,
        # is still booting: say "starting", not "not reachable".
        if not _EVER_UP and time.monotonic() - _STARTED < STARTING_GRACE_S:
            return "starting", ()
        return "down", ()
    _EVER_UP = True
    names = tuple(
        str(m.get("name") or m.get("model"))
        for m in (body.get("models") or [])
        if isinstance(m, dict) and (m.get("name") or m.get("model"))
    )
    wanted = settings.local_llm_model
    if any(same_model(n, wanted) for n in names):
        return "ready", names
    return "pulling", names


async def local_status(
    settings: Any,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    warm: bool = True,
) -> LocalStatus:
    """Ask the server and refresh the cache. `not_configured` without any I/O when the
    local model is not part of this install. The first time the model turns ready it is
    loaded into memory once (`warm`)."""
    now = datetime.now(UTC)
    model = str(settings.local_llm_model)
    if not _enabled(settings):
        return LocalStatus(False, "not_configured", model, (), now)
    state, names = await _probe(settings, transport)
    global _CACHE
    same = _CACHE.url == settings.local_llm_url and _CACHE.model == settings.local_llm_model
    # What the model's load looked like at the last check: only meaningful while the server
    # keeps listing it (a restarted or stopped server has dropped it from memory).
    warmed = same and _CACHE.warmed and state == "ready"
    failed = same and _CACHE.warm_failed and state == "ready" and not warmed
    if state == "ready" and not warmed and warm and not _WARM_LOCK.locked():
        warmed = await warm_up(settings, transport=transport)
        failed = not warmed
    if state == "ready" and failed:
        state = "down"  # listed but cannot load: never answer a free business with it
    _CACHE = _Cache(
        state=state,
        models=names,
        at_monotonic=time.monotonic(),
        checked_at=now,
        url=settings.local_llm_url,
        model=settings.local_llm_model,
        warmed=warmed,
        warm_failed=failed,
    )
    if state == "down":
        logger.info("Local model server not reachable at %s", safe_address(settings.local_llm_url))
    return LocalStatus(True, state, model, names, now)


def serves_locally(llm: Any) -> bool:
    """Whether `llm` calls the local model (its provider is the local route's)."""
    return getattr(llm, "provider_name", None) == PROVIDER


def mark_unavailable(settings: Any) -> None:
    """An answer on the local model just failed (it would not load, the server stopped): stop
    calling it ready now, without waiting for the next refresh. The refresher looks again, loads
    the model again and turns it ready when it can answer."""
    global _CACHE
    if not _enabled(settings) or not _fresh(settings):
        return
    _CACHE = replace(_CACHE, state="down", warmed=False, warm_failed=True)


async def ensure_status(settings: Any) -> None:
    """For processes without the API's refresher (Celery workers): look once when the cache
    is missing or stale. Never warms the model, never raises."""
    if not _enabled(settings) or _fresh(settings):
        return
    try:
        await local_status(settings, warm=False)
    except Exception:
        logger.warning("Local model check failed", exc_info=True)


async def warm_up(settings: Any, *, transport: httpx.AsyncBaseTransport | None = None) -> bool:
    """Load the model into memory (an empty generate, `keep_alive` as configured). Best effort;
    one warm-up at a time (a second caller returns at once)."""
    if _WARM_LOCK.locked():
        return False
    async with _WARM_LOCK:
        return await _warm_up(settings, transport)


async def _warm_up(settings: Any, transport: httpx.AsyncBaseTransport | None) -> bool:
    try:
        async with httpx.AsyncClient(
            base_url=settings.local_llm_url,
            timeout=float(settings.local_llm_timeout_seconds),
            transport=transport,
        ) as client:
            resp = await client.post(
                "/api/generate",
                json={
                    "model": settings.local_llm_model,
                    "prompt": "",
                    "keep_alive": settings.local_llm_keep_alive,
                },
            )
            resp.raise_for_status()
    except httpx.HTTPError:
        logger.warning(
            "Could not warm up the local model at %s", safe_address(settings.local_llm_url)
        )
        return False
    logger.info("Local model %s loaded", settings.local_llm_model)
    return True


async def _refresh_forever(settings: Any) -> None:
    while True:
        try:
            status = await local_status(settings)
        except Exception:  # never let the refresher die
            logger.warning("Local model check failed", exc_info=True)
            status = None
        if status is not None and status.state == "ready":
            pause = READY_TTL_S
        elif _CACHE.warm_failed:
            pause = LOAD_FAILED_TTL_S
        else:
            pause = NOT_READY_TTL_S
        await asyncio.sleep(pause)


def start_refresher(app: Any) -> "asyncio.Task[None] | None":
    """Start the background check when the local model is part of this install. The task is
    kept on `app.state` (a bare task may be garbage collected) and cancelled by `stop_refresher`."""
    settings = app.state.settings
    if not _enabled(settings):
        app.state.local_llm_task = None
        return None
    task = asyncio.create_task(_refresh_forever(settings))
    app.state.local_llm_task = task
    return task


async def stop_refresher(app: Any) -> None:
    task = getattr(app.state, "local_llm_task", None)
    if task is None:
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    app.state.local_llm_task = None
