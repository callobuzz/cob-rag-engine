"""Guided setup routes take every key kind and are scoped like every per-KB route."""

import uuid

import pytest

from tests.unit.scope_harness import MATRIX, call, check, expected, scoped_app

pytestmark = pytest.mark.usefixtures("unlimited_plan")
X = uuid.uuid4()

ROUTES = [
    ("POST", "/api/v1/corpora/{c}/assistant/setup", {"business_type": "clinic"}),
    ("GET", f"/api/v1/corpora/{{c}}/assistant/setup/{X}", None),
    ("GET", f"/api/v1/corpora/{{c}}/assistant/setup/{X}/questions", None),
    ("GET", f"/api/v1/corpora/{{c}}/assistant/setup/{X}/gaps", None),
    ("POST", f"/api/v1/corpora/{{c}}/assistant/setup/{X}/apply", {"base_revision": 1}),
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
