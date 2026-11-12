"""Assistant routes take every key kind and are scoped like every per-KB route."""

import pytest

from tests.unit.scope_harness import MATRIX, call, check, expected, scoped_app

pytestmark = pytest.mark.usefixtures("unlimited_plan")

ROUTES = [
    ("GET", "/api/v1/corpora/{c}/assistant", None),
    ("PUT", "/api/v1/corpora/{c}/assistant/draft", {"config": {}, "base_revision": 0}),
    ("PATCH", "/api/v1/corpora/{c}/assistant/draft", {"patch": {"voice": {"emoji": "light"}}}),
    ("POST", "/api/v1/corpora/{c}/assistant/draft/from-template", {"template": "clinic"}),
    ("DELETE", "/api/v1/corpora/{c}/assistant/draft", None),
    ("POST", "/api/v1/corpora/{c}/assistant/publish", {"base_revision": 0}),
    ("GET", "/api/v1/corpora/{c}/assistant/versions", None),
    ("GET", "/api/v1/corpora/{c}/assistant/versions/1", None),
    ("POST", "/api/v1/corpora/{c}/assistant/versions/1/restore", None),
    ("POST", "/api/v1/corpora/{c}/assistant/enabled", {"enabled": False}),
    ("POST", "/api/v1/corpora/{c}/assistant/compile", {}),
]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
@pytest.mark.parametrize(("key", "which", "outcome"), MATRIX)
async def test_scoping(
    method: str, path: str, body: object, key: str | None, which: str, outcome: str
) -> None:
    with scoped_app() as (app, world):
        url = path.format(c=world.corpus(which).id)
        res = await call(app, key, method, url, json=body)
    check(expected(key, outcome, method, url, body), res.status_code, res.text)
