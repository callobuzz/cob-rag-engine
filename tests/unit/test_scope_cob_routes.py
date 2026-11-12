"""COB routes are for people: every key kind is refused, sessions see only their own data."""

import uuid

import pytest

from tests.unit.scope_harness import call

MID, CID = uuid.uuid4(), uuid.uuid4()
ROUTES = [
    ("POST", "/api/v1/cob/chat", {"message": "hi"}),
    ("GET", "/api/v1/cob/conversations", None),
    ("GET", f"/api/v1/cob/conversations/{CID}", None),
    ("DELETE", f"/api/v1/cob/conversations/{CID}", None),
    ("POST", f"/api/v1/cob/messages/{MID}/feedback", {"rating": 1}),
    ("GET", "/api/v1/cob/starters?route=/", None),
    ("POST", f"/api/v1/cob/actions/{MID}/confirm", {}),  # COB5
    ("POST", f"/api/v1/cob/actions/{MID}/cancel", None),  # COB5
]
KEYS = ["admin", "tk_a", "tk_b", "ck_a", "ck_b"]


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
async def test_every_key_kind_is_refused(cob, key, method, path, body) -> None:  # type: ignore[no-untyped-def]
    res = await call(cob.app, key, method, path, json=body)
    assert res.status_code == 403, (key, path, res.text)
    assert res.json()["code"] == "session_required"
    assert cob.director.built == 0


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
async def test_no_credential_is_401(cob, method, path, body) -> None:  # type: ignore[no-untyped-def]
    res = await call(cob.app, None, method, path, json=body)
    assert res.status_code == 401


@pytest.mark.parametrize("key", ["alice", "ravi", "sa", "super_op"])
async def test_every_session_may_use_cob_on_its_own_data(cob, key) -> None:  # type: ignore[no-untyped-def]
    assert (await cob.get(key, "/api/v1/cob/conversations")).status_code == 200
    assert (await cob.get(key, "/api/v1/cob/starters?route=/")).status_code == 200
    assert (await cob.chat(key, {"message": "hi"})).status_code == 200
    # what is not theirs is simply not there
    assert (await cob.get(key, f"/api/v1/cob/conversations/{CID}")).status_code == 404
    assert (await cob.delete(key, f"/api/v1/cob/conversations/{CID}")).status_code == 404
    res = await cob.post(key, f"/api/v1/cob/messages/{MID}/feedback", {"rating": 1})
    assert res.status_code == 404


async def test_the_session_roles_are_what_the_workspace_says(cob) -> None:  # type: ignore[no-untyped-def]
    roles = {}
    for key in ("alice", "ravi", "sa", "super_op"):
        events = await cob.sse(key, {"message": "hi"})
        roles[key] = events[0].data["role"]
    assert roles == {
        "alice": "owner",
        "ravi": "member",
        "sa": "admin",
        "super_op": "operator_owner",
    }
