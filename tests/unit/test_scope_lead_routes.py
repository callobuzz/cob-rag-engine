"""Who may read which leads (conversation C4): the same matrix as every scoped route."""

import uuid
from typing import Any

import pytest
from cryptography.fernet import Fernet

from tests.unit.scope_harness import MATRIX, call, check, expected, scoped_app

X = uuid.uuid4()
ROUTES = [
    ("GET", "/api/v1/leads?corpus_id={c}", None),
    ("GET", "/api/v1/leads/export.csv?corpus_id={c}", None),
]


def _with_key(app: Any) -> None:
    app.state.settings = app.state.settings.model_copy(
        update={"encryption_key": Fernet.generate_key().decode()}
    )


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
@pytest.mark.parametrize(("key", "which", "outcome"), MATRIX)
async def test_scoping(
    method: str, path: str, body: Any, key: str | None, which: str, outcome: str
) -> None:
    with scoped_app() as (app, world):
        _with_key(app)
        url = path.format(c=world.corpus(which).id)
        res = await call(app, key, method, url, json=body)
    check(expected(key, outcome, method, url, body), res.status_code, res.text)


@pytest.mark.parametrize("key", ["admin", "tk_a", "ck_a", "owner_a", "member_a"])
async def test_unknown_lead_is_404(key: str) -> None:
    with scoped_app() as (app, _):
        _with_key(app)
        res = await call(app, key, "GET", f"/api/v1/leads/{X}")
    assert res.status_code == 404 and res.json()["code"] == "lead_not_found"


async def test_a_lead_of_another_tenant_looks_like_a_missing_one() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    with scoped_app() as (app, world):
        _with_key(app)
        theirs = SimpleNamespace(id=X, corpus_id=world.corpus("b").id, tenant_id=uuid.uuid4())
        with (
            patch.object(world.session, "get", AsyncMock(return_value=theirs)),
            patch(
                "callobuzz_rag.api.routes.leads.authorized_corpus_ids",
                AsyncMock(return_value=[world.corpus("a").id]),
            ),
        ):
            for method in ("GET", "PATCH", "DELETE"):
                res = await call(
                    app,
                    "owner_a" if method == "DELETE" else "tk_a",
                    method,
                    f"/api/v1/leads/{X}",
                    json={"status": "won"} if method == "PATCH" else None,
                )
                assert res.status_code == 404, (method, res.text)


async def test_members_work_leads_but_cannot_delete_or_export() -> None:
    with scoped_app() as (app, world):
        _with_key(app)
        c = world.corpus("a").id
        listing = await call(app, "member_a", "GET", f"/api/v1/leads?corpus_id={c}")
        assert listing.status_code == 200
        res = await call(app, "member_a", "DELETE", f"/api/v1/leads/{X}")
        assert res.status_code == 403 and res.json()["code"] == "owner_required"
        res = await call(app, "member_a", "GET", f"/api/v1/leads/export.csv?corpus_id={c}")
        assert res.status_code == 403 and res.json()["code"] == "owner_required"
        res = await call(app, "member_a", "PATCH", f"/api/v1/leads/{X}", json={"status": "won"})
        assert res.status_code == 404  # allowed in, nothing there
