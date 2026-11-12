"""Website-learning routes take every key kind, scoped like every corpus route (BIZ1).

Admin and tenant keys and sessions name the knowledge base in the path; a corpus key reaches
only its own; a member's session is refused the owner-only routes (`OWNER_ONLY` in
`scope_harness.py`): changing what is learned, removing a site, leaving a page out.
"""

import uuid

import pytest

from tests.unit.scope_harness import MATRIX, call, check, expected, scoped_app

X = uuid.uuid4()  # an id that exists nowhere

ROUTES = [
    ("GET", "/api/v1/corpora/{c}/website", None),
    ("PATCH", "/api/v1/corpora/{c}/website", {"enabled": False}),
    ("POST", "/api/v1/corpora/{c}/website/learn", {"origin": "shreedental.in"}),
    ("POST", "/api/v1/corpora/{c}/website/reread", None),
    ("DELETE", f"/api/v1/corpora/{{c}}/website/sources/{X}", None),
    ("GET", "/api/v1/corpora/{c}/website/pages", None),
    ("POST", f"/api/v1/corpora/{{c}}/website/pages/{X}/reread", None),
    ("PATCH", f"/api/v1/corpora/{{c}}/website/pages/{X}", {"excluded": True}),
    # BIZ2 freshness
    ("PATCH", "/api/v1/corpora/{c}/website", {"refresh_mode": "weekly"}),
    ("POST", "/api/v1/corpora/{c}/website/learn-key", None),
    ("DELETE", "/api/v1/corpora/{c}/website/learn-key", None),
    # /BIZ2 freshness
    ("GET", "/api/v1/corpora/{c}/website/suggestions", None),  # BIZ4
]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
@pytest.mark.parametrize(("key", "which", "outcome"), MATRIX)
async def test_scoping(method, path, body, key, which, outcome) -> None:
    with scoped_app() as (app, world):
        url = path.format(c=world.corpus(which).id)
        res = await call(app, key, method, url, json=body)
    check(expected(key, outcome, method, url, body), res.status_code, res.text)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PATCH", "/api/v1/corpora/{c}/website", {"enabled": False, "surprise": 1}),
        ("POST", "/api/v1/corpora/{c}/website/learn", {"origin": "shreedental.in", "x": 1}),
        ("PATCH", f"/api/v1/corpora/{{c}}/website/pages/{X}", {"excluded": True, "x": 1}),
        ("PATCH", "/api/v1/corpora/{c}/website", {"refresh_mode": "daily", "x": 1}),  # BIZ2
    ],
)
async def test_bodies_reject_unknown_fields(method, path, body) -> None:
    with scoped_app() as (app, world):
        res = await call(app, "admin", method, path.format(c=world.corpus_a.id), json=body)
    assert res.status_code == 422
    assert "extra_forbidden" in res.text
