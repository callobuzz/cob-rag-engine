"""`/api/v1/health/ready`: 503 when Postgres, Redis or Qdrant is down, public like /health."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from callobuzz_rag.api.app import create_app
from callobuzz_rag.api.auth import PUBLIC_PATHS
from callobuzz_rag.api.permissions import PUBLIC_ROUTES

_MODULE = "callobuzz_rag.api.routes.health"


@pytest.fixture
async def client():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _get(client, path, *, postgres=True, redis=True, qdrant=True, storage=True):
    with (
        patch(f"{_MODULE}._check_postgres", new=AsyncMock(return_value=postgres)),
        patch(f"{_MODULE}._check_redis", new=AsyncMock(return_value=redis)),
        patch(f"{_MODULE}._check_qdrant", new=AsyncMock(return_value=qdrant)),
        patch(f"{_MODULE}._check_object_storage", new=AsyncMock(return_value=storage)),
    ):
        return await client.get(path)


async def test_ready_is_200_when_everything_answers(client):
    resp = await _get(client, "/api/v1/health/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


@pytest.mark.parametrize("down", ["postgres", "redis", "qdrant"])
async def test_ready_is_503_when_a_required_service_is_down(client, down):
    resp = await _get(client, "/api/v1/health/ready", **{down: False})
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["services"][down] is False


async def test_ready_ignores_object_storage(client):
    resp = await _get(client, "/api/v1/health/ready", storage=False)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


async def test_liveness_still_reports_object_storage(client):
    resp = await _get(client, "/api/v1/health", storage=False)
    assert resp.status_code == 200
    assert resp.json()["services"]["storage"] is False


async def test_liveness_stays_200_when_degraded(client):
    resp = await _get(client, "/api/v1/health", redis=False)
    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"


def test_ready_is_public_like_health():
    assert "/api/v1/health/ready" in PUBLIC_PATHS
    assert ("GET", "/api/v1/health/ready") in PUBLIC_ROUTES


async def test_ready_needs_no_credential(client):
    """No X-API-Key: the auth middleware must let it through, not answer 401."""
    resp = await _get(client, "/api/v1/health/ready", redis=False)
    assert resp.status_code == 503


# --- The real probes: a failure or a hang must become "down", never an error or a stall.


def _ok_session_factory():
    session = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


def _fake_redis(*, ping_error=None):
    client = MagicMock()
    client.ping = AsyncMock(side_effect=ping_error)
    client.aclose = AsyncMock()
    return client


def _fake_qdrant(*, error=None):
    client = MagicMock()
    client.get_collections = AsyncMock(side_effect=error)
    client.close = AsyncMock()
    return client


@pytest.fixture
def healthy(client):
    """A working Postgres session; tests break the other probes one at a time."""
    from callobuzz_rag.core.config import Settings

    client._transport.app.state.session_factory = _ok_session_factory()
    client._transport.app.state.settings = Settings()


async def test_real_probes_all_healthy_is_ready(client, healthy):
    with (
        patch("redis.asyncio.from_url", return_value=_fake_redis()),
        patch("qdrant_client.AsyncQdrantClient", return_value=_fake_qdrant()),
        patch(f"{_MODULE}._check_object_storage", new=AsyncMock(return_value=True)),
    ):
        resp = await client.get("/api/v1/health/ready")
    assert resp.status_code == 200
    assert resp.json()["services"] == {
        "api": True,
        "postgres": True,
        "redis": True,
        "qdrant": True,
    }


async def test_redis_error_means_redis_false_and_503(client, healthy):
    with (
        patch("redis.asyncio.from_url", return_value=_fake_redis(ping_error=OSError("refused"))),
        patch("qdrant_client.AsyncQdrantClient", return_value=_fake_qdrant()),
    ):
        resp = await client.get("/api/v1/health/ready")
    assert resp.status_code == 503
    assert resp.json()["services"]["redis"] is False
    assert resp.json()["services"]["qdrant"] is True


async def test_qdrant_error_means_qdrant_false_and_client_is_closed(client, healthy):
    qdrant = _fake_qdrant(error=RuntimeError("down"))
    with (
        patch("redis.asyncio.from_url", return_value=_fake_redis()),
        patch("qdrant_client.AsyncQdrantClient", return_value=qdrant),
    ):
        resp = await client.get("/api/v1/health/ready")
    assert resp.status_code == 503
    assert resp.json()["services"]["qdrant"] is False
    qdrant.close.assert_awaited_once()


async def test_postgres_error_means_postgres_false_and_503(client, healthy):
    client._transport.app.state.session_factory = MagicMock(side_effect=RuntimeError("down"))
    with (
        patch("redis.asyncio.from_url", return_value=_fake_redis()),
        patch("qdrant_client.AsyncQdrantClient", return_value=_fake_qdrant()),
    ):
        resp = await client.get("/api/v1/health/ready")
    assert resp.status_code == 503
    assert resp.json()["services"]["postgres"] is False


async def test_a_hung_probe_times_out_and_the_endpoint_still_answers_503(client, healthy):
    async def hang(*_a, **_k):
        await asyncio.sleep(30)

    slow = _fake_redis()
    slow.ping = hang
    with (
        patch(f"{_MODULE}.PROBE_TIMEOUT_S", 0.05),
        patch("redis.asyncio.from_url", return_value=slow),
        patch("qdrant_client.AsyncQdrantClient", return_value=_fake_qdrant()),
    ):
        started = time.monotonic()
        resp = await client.get("/api/v1/health/ready")
    assert time.monotonic() - started < 2
    assert resp.status_code == 503
    assert resp.json()["services"]["redis"] is False


async def test_probes_run_concurrently(client, healthy):
    async def slow_ping():
        await asyncio.sleep(0.2)

    redis = _fake_redis()
    redis.ping = slow_ping
    qdrant = _fake_qdrant()

    async def slow_collections():
        await asyncio.sleep(0.2)

    qdrant.get_collections = slow_collections
    with (
        patch("redis.asyncio.from_url", return_value=redis),
        patch("qdrant_client.AsyncQdrantClient", return_value=qdrant),
    ):
        started = time.monotonic()
        resp = await client.get("/api/v1/health/ready")
    assert resp.status_code == 200
    assert time.monotonic() - started < 0.38


async def test_redis_probe_sets_socket_timeouts(client, healthy):
    with (
        patch("redis.asyncio.from_url", return_value=_fake_redis()) as from_url,
        patch("qdrant_client.AsyncQdrantClient", return_value=_fake_qdrant()),
    ):
        await client.get("/api/v1/health/ready")
    kwargs = from_url.call_args.kwargs
    assert kwargs["socket_connect_timeout"] <= 3
    assert kwargs["socket_timeout"] <= 3


async def test_readiness_does_not_probe_object_storage(client):
    storage = AsyncMock(return_value=False)
    with (
        patch(f"{_MODULE}._check_postgres", new=AsyncMock(return_value=True)),
        patch(f"{_MODULE}._check_redis", new=AsyncMock(return_value=True)),
        patch(f"{_MODULE}._check_qdrant", new=AsyncMock(return_value=True)),
        patch(f"{_MODULE}._check_object_storage", new=storage),
    ):
        resp = await client.get("/api/v1/health/ready")
    assert resp.status_code == 200
    storage.assert_not_awaited()
    assert "storage" not in resp.json()["services"]


def test_health_status_is_a_closed_set_in_the_schema():
    from callobuzz_rag.api.schemas.health import HealthResponse

    enum = HealthResponse.model_json_schema()["properties"]["status"]["enum"]
    assert set(enum) == {"ok", "degraded", "ready", "not_ready"}
