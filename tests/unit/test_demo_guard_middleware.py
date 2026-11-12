import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.demo import guard

ID = "00000000-0000-0000-0000-000000000001"
METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


async def _ok(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


@pytest.fixture
def client() -> TestClient:
    inner = Starlette(routes=[Route("/{path:path}", _ok, methods=METHODS)])
    return TestClient(guard.DemoGuardMiddleware(inner, admin_key="adm_secret"))


def test_refused_with_the_one_message(client: TestClient) -> None:
    r = client.delete(f"/api/v1/corpora/{ID}")
    assert r.status_code == 403
    assert r.json() == {"detail": guard.MESSAGE, "code": "demo_read_only"}


def test_admin_key_passes_and_a_wrong_key_does_not(client: TestClient) -> None:
    ok = client.delete(f"/api/v1/corpora/{ID}", headers={"X-API-Key": "adm_secret"})
    assert ok.status_code == 200
    wrong = client.delete(f"/api/v1/corpora/{ID}", headers={"X-API-Key": "adm_secreT"})
    assert wrong.status_code == 403
    bearer = client.delete(f"/api/v1/corpora/{ID}", headers={"Authorization": "Bearer adm_secret"})
    assert bearer.status_code == 403


def test_an_empty_admin_key_exempts_nobody() -> None:
    inner = Starlette(routes=[Route("/{path:path}", _ok, methods=METHODS)])
    open_client = TestClient(guard.DemoGuardMiddleware(inner, admin_key=""))
    assert open_client.delete(f"/api/v1/corpora/{ID}", headers={"X-API-Key": ""}).status_code == 403


def test_reads_and_answers_pass(client: TestClient) -> None:
    assert client.get("/api/v1/admin/tenants").status_code == 200
    assert client.post("/api/v1/query", json={"query": "timings?"}).status_code == 200
    assert client.post("/api/v1/widget/chat", json={}).status_code == 200


def test_the_body_is_never_read(client: TestClient) -> None:
    # A refused upload is answered without consuming the stream.
    r = client.post("/api/v1/billing/top-up-requests", content=b"x" * 5_000_000)
    assert r.status_code == 403


def test_installed_only_in_demo_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from callobuzz_rag.api.app import create_app
    from callobuzz_rag.api.auth import APIKeyAuthMiddleware

    assert all(m.cls is not guard.DemoGuardMiddleware for m in create_app().user_middleware)
    for key, value in {
        "RAG_DEMO_MODE": "true",
        "RAG_SUPERADMIN_EMAIL": "op@sahayak.example.com",
        "RAG_DEMO_OWNER_EMAIL": "owner@nirmalclinic.example.com",
        "RAG_DEMO_PASSWORD": "Nirmal-Clinic-Demo-2026",
        "RAG_ADMIN_API_KEY": "adm_" + "x" * 40,
        "RAG_SESSION_SECRET": "s" * 48,
    }.items():
        monkeypatch.setenv(key, value)
    classes = [m.cls for m in create_app().user_middleware]
    assert guard.DemoGuardMiddleware in classes
    # user_middleware lists the outermost first, so a request meets the auth layer and then
    # the guard: the guard is the entry right after auth (added just before it).
    assert classes.index(guard.DemoGuardMiddleware) == classes.index(APIKeyAuthMiddleware) + 1


def test_field_refusal() -> None:
    with pytest.raises(APIError) as err:
        guard.raise_for_fields(
            demo_mode=True,
            method="PUT",
            template="/api/v1/corpora/{corpus_id}",
            fields_set={"llm_config"},
        )
    assert err.value.status_code == 403 and err.value.code == "demo_read_only"
    guard.raise_for_fields(
        demo_mode=False,
        method="PUT",
        template="/api/v1/corpora/{corpus_id}",
        fields_set={"llm_config"},
    )
    guard.raise_for_fields(
        demo_mode=True,
        method="PUT",
        template="/api/v1/corpora/{corpus_id}",
        fields_set={"llm_config"},
        admin_key=True,
    )


async def test_refuse_hidden_fields_applies_the_demo_rule() -> None:
    from types import SimpleNamespace

    from pydantic import BaseModel

    from callobuzz_rag.api.features import refuse_hidden_fields
    from callobuzz_rag.core.config import Settings

    class Body(BaseModel):
        name: str | None = None
        llm_config: dict[str, str] | None = None

    demo = Settings(
        demo_mode=True,
        superadmin_email="op@sahayak.example.com",
        demo_owner_email="owner@nirmalclinic.example.com",
        demo_password="Nirmal-Clinic-Demo-2026",
        admin_api_key="adm_" + "x" * 40,
        session_secret="s" * 48,
    )

    def request(settings: Settings, key: str | None = None) -> SimpleNamespace:
        headers = {"x-api-key": key} if key else {}
        return SimpleNamespace(
            method="PUT",
            url=SimpleNamespace(path=f"/api/v1/corpora/{ID}"),
            headers=headers,
            state=SimpleNamespace(),
            app=SimpleNamespace(state=SimpleNamespace(settings=settings)),
        )

    with pytest.raises(APIError) as err:
        await refuse_hidden_fields(request(demo), Body(llm_config={"model": "x"}))  # type: ignore[arg-type]
    assert err.value.code == "demo_read_only"
    await refuse_hidden_fields(request(demo), Body(name="Clinic"))  # type: ignore[arg-type]
    await refuse_hidden_fields(
        request(demo, "adm_" + "x" * 40),  # type: ignore[arg-type]
        Body(llm_config={"model": "x"}),
    )
    await refuse_hidden_fields(request(Settings()), Body(llm_config={"model": "x"}))  # type: ignore[arg-type]
