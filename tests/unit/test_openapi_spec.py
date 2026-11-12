"""docs/openapi.json must match the app, since client packages generate their types from it."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "export_openapi.py"


def _exporter():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("export_openapi", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_spec_is_current() -> None:
    exporter = _exporter()
    committed = exporter.SPEC_PATH.read_text(encoding="utf-8")
    assert committed == exporter.render(), (
        "docs/openapi.json is out of date: run python scripts/export_openapi.py"
    )


def test_spec_documents_the_key_routes() -> None:
    import json

    paths = json.loads(_exporter().SPEC_PATH.read_text(encoding="utf-8"))["paths"]
    for route in ("/api/v1/whoami", "/api/v1/query", "/api/v1/query/retrieve", "/api/v1/ingest"):
        assert route in paths, route


def test_spec_declares_bearer_sessions() -> None:
    import json

    spec = json.loads(_exporter().SPEC_PATH.read_text(encoding="utf-8"))
    assert spec["components"]["securitySchemes"]["BearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": "Dashboard sign-in access token (POST /api/v1/auth/login).",
    }
    paths = spec["paths"]
    assert paths["/api/v1/auth/login"]["post"]["security"] == []
    assert paths["/api/v1/auth/reset"]["post"]["security"] == []
    assert paths["/api/v1/auth/me"]["get"]["security"] == [{"BearerAuth": []}]
    assert paths["/api/v1/docs/search"]["get"]["security"] == [{"BearerAuth": []}]
    assert paths["/api/v1/cob/chat"]["post"]["security"] == [{"BearerAuth": []}]
    assert paths["/api/v1/query"]["post"]["security"] == [{"ApiKey": []}, {"BearerAuth": []}]
