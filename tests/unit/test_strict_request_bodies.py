"""Unknown fields in a request body are a 422 that names them (decided 2026-10-02).

Every request body inherits `StrictRequest`. These go through the real app: a field the model
does not declare used to be dropped and the request succeeded; now FastAPI answers 422 with an
`extra_forbidden` entry whose `loc` ends in the field, which is what the dashboard maps to the
form field and what the SDKs surface.
"""

import pytest

from tests.unit.scope_harness import call, scoped_app

ROUTES = [
    ("POST", "/api/v1/corpora", {"name": "X"}),
    (
        "POST",
        "/api/v1/webhooks",
        {"url": "https://example.com/hook", "events": ["ingest.completed"]},
    ),
    ("POST", "/api/v1/ingest/records", {"records": [{"id": "a", "text": "t"}]}),
    ("POST", "/api/v1/admin/tenants", {"name": "T"}),
]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
async def test_an_unknown_field_is_a_422_naming_it(method: str, path: str, body: dict) -> None:
    with scoped_app() as (app, _):
        res = await call(app, "admin", method, path, json={**body, "surprise_field": 1})
    assert res.status_code == 422, res.text
    detail = res.json()["detail"]
    entry = next(e for e in detail if e["type"] == "extra_forbidden")
    assert entry["loc"][-1] == "surprise_field"
    assert "surprise_field" in res.text


async def test_an_unknown_field_inside_a_nested_object_is_named_too() -> None:
    with scoped_app() as (app, _):
        res = await call(
            app,
            "admin",
            "POST",
            "/api/v1/ingest/records",
            json={"records": [{"id": "a", "text": "t", "titel": "typo"}]},
        )
    assert res.status_code == 422, res.text
    entry = next(e for e in res.json()["detail"] if e["type"] == "extra_forbidden")
    assert entry["loc"][-1] == "titel" and "records" in entry["loc"]


async def test_the_declared_fields_still_work() -> None:
    with scoped_app() as (app, _):
        res = await call(
            app, "admin", "POST", "/api/v1/ingest/records", json={"records": [{"id": "a"}]}
        )
    assert res.status_code != 422 or "extra_forbidden" not in res.text
