"""The three things the engine asks of its Redis-compatible store, against the real Valkey image.

Password-protected `redis.asyncio` SET/GET (rate limits, caches), a Celery task sent through the
broker URL from `Settings.redis_url` and read back (the job queue), and the lock celery-redbeat
takes so only one beat scheduler runs. Needs Docker (skipped with the integration suite when down).
"""

import pytest
from testcontainers.redis import RedisContainer

from tests.integration.conftest import VALKEY_IMAGE

pytestmark = pytest.mark.integration

PASSWORD = "valkey-smoke-password"


@pytest.fixture(scope="module")
def valkey():
    with RedisContainer(VALKEY_IMAGE, password=PASSWORD) as container:
        yield container


def _settings(valkey: RedisContainer):
    from callobuzz_rag.core.config import Settings

    return Settings(
        _env_file=None,
        redis_host=valkey.get_container_host_ip().replace("localhost", "127.0.0.1"),
        redis_port=int(valkey.get_exposed_port(6379)),
        redis_password=PASSWORD,
    )


async def test_password_protected_set_and_get(valkey: RedisContainer) -> None:
    import redis.asyncio as aioredis

    settings = _settings(valkey)
    client = aioredis.from_url(settings.redis_url)
    try:
        await client.set("smoke:key", "value", ex=30)
        assert await client.get("smoke:key") == b"value"
    finally:
        await client.aclose()
    anonymous = aioredis.Redis(host=settings.redis_host, port=settings.redis_port)
    try:
        with pytest.raises(aioredis.AuthenticationError):
            await anonymous.get("smoke:key")
    finally:
        await anonymous.aclose()


def test_celery_task_goes_through_the_broker(valkey: RedisContainer) -> None:
    from celery import Celery

    settings = _settings(valkey)
    app = Celery("smoke", broker=settings.redis_url)
    app.send_task("smoke.task", args=[1, 2], queue="smoke-queue")
    with app.connection_for_read() as conn:
        queue = conn.SimpleQueue("smoke-queue")
        try:
            message = queue.get(block=True, timeout=10)
            assert message.headers["task"] == "smoke.task"
            message.ack()
        finally:
            queue.close()


def test_redbeat_takes_and_releases_its_own_lock(valkey: RedisContainer) -> None:
    from celery import Celery
    from redbeat.schedulers import RedBeatScheduler, acquire_distributed_beat_lock, get_redis

    settings = _settings(valkey)
    app = Celery("smoke", broker=settings.redis_url)
    app.conf.redbeat_redis_url = settings.redis_url
    scheduler = RedBeatScheduler(app=app, lazy=True)

    class _Service:  # what celery's beat_init signal passes as the sender
        pass

    service = _Service()
    service.scheduler = scheduler  # type: ignore[attr-defined]
    client = get_redis(app)
    acquire_distributed_beat_lock(sender=service)
    try:
        assert scheduler.lock is not None
        assert client.exists(scheduler.lock_key) == 1
    finally:
        scheduler.lock.release()
    assert client.exists(scheduler.lock_key) == 0
