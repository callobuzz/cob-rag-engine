"""A browser must be able to read the engine's 401 for a wrong key.

CORS used to sit inside the auth middleware, so a rejected key produced a 401
with no Access-Control-Allow-Origin header; the browser then hid it and a
dashboard could only report "Failed to fetch" instead of "wrong key".
"""

from httpx import ASGITransport, AsyncClient

from callobuzz_rag.api.app import create_app
from callobuzz_rag.core.config import Settings

ORIGIN = "http://127.0.0.1:8090"


async def _get(headers: dict[str, str]) -> tuple[int, str | None]:
    app = create_app()
    app.state.settings = Settings()  # lifespan does not run under ASGITransport
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/v1/whoami", headers={"Origin": ORIGIN, **headers})
    return res.status_code, res.headers.get("access-control-allow-origin")


async def test_wrong_key_401_carries_cors_headers() -> None:
    status, allow = await _get({"X-API-Key": "not-a-real-key"})
    assert status == 401
    assert allow in {"*", ORIGIN}


async def test_missing_key_401_carries_cors_headers() -> None:
    status, allow = await _get({})
    assert status == 401
    assert allow in {"*", ORIGIN}
