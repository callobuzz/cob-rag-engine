"""What the demo routes, the scheduler and the CLI share: the Redis client, the status, a run."""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from callobuzz_rag.api.schemas.demo import DemoStatusOut
from callobuzz_rag.core.exceptions import RAGKernelError
from callobuzz_rag.demo import limits, reset
from callobuzz_rag.demo.budget import DemoBudget

logger = logging.getLogger(__name__)


class DemoUnavailableError(RAGKernelError):
    """Redis did not answer."""


def get_redis(app: Any) -> Any:
    """One client for the API process, kept on `app.state`."""
    client = getattr(app.state, "demo_redis", None)
    if client is None:
        import redis.asyncio as aioredis

        client = aioredis.from_url(  # type: ignore[no-untyped-call]
            app.state.settings.redis_url, socket_timeout=2, socket_connect_timeout=2
        )
        app.state.demo_redis = client
    return client


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _when(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def read_status(app: Any, *, now: datetime | None = None) -> DemoStatusOut:
    """The demo's status. Raises `DemoUnavailableError` when Redis does not answer."""
    settings = app.state.settings
    redis = get_redis(app)
    now = now or datetime.now(UTC)
    try:
        lock = await redis.get(reset.LOCK_KEY)
        raw = _text(await redis.get(reset.STATUS_KEY))
        last = _text(await redis.get(reset.LAST_RESET_KEY))
    except Exception as exc:
        raise DemoUnavailableError("the queue server did not answer") from exc
    result: dict[str, Any] = {}
    if raw:
        try:
            parsed = json.loads(raw)
            result = parsed if isinstance(parsed, dict) else {}
        except ValueError:
            result = {}
    used = await DemoBudget(
        redis, settings.demo_model_calls_per_day, reset_at=settings.demo_reset_at_utc
    ).used_today()
    answers = await limits.limiter_from(redis, settings).counts()
    return DemoStatusOut(
        enabled=bool(settings.demo_mode),
        running=lock is not None,
        last_reset_at=_when(last),
        last_result=result.get("result") if isinstance(result.get("result"), str) else None,
        last_failed_step=(
            result.get("failed_step") if isinstance(result.get("failed_step"), str) else None
        ),
        next_reset_at=reset.next_reset_at(settings.demo_reset_at_utc, now),
        model_calls_today=used,
        model_calls_limit=settings.demo_model_calls_per_day,
        answers_today=answers[0] if answers is not None else None,
        answers_limit=settings.demo_answers_per_day,
        answers_per_visitor_limit=settings.demo_answers_per_visitor_per_day,
    )


async def run_for_app(app: Any, trigger: str) -> reset.ResetReport:
    """One reset of this app's demo, as the admin credential, through the app itself."""
    settings = app.state.settings
    client = reset.InternalDemoClient(app, settings.admin_api_key.get_secret_value())
    deps = reset.ResetDeps.from_app(app, settings)
    return await reset.run_reset(client, get_redis(app), deps, trigger=trigger)
