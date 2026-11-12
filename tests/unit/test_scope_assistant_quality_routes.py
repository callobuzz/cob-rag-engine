"""C7 routes take every key kind, scoped to the knowledge base in the path."""

import uuid

import pytest

from tests.unit.scope_harness import MATRIX, call, check, expected, scoped_app

pytestmark = pytest.mark.usefixtures("unlimited_plan")

X = uuid.uuid4()

ROUTES = [
    ("GET", "/api/v1/corpora/{c}/golden-sets", None),
    ("POST", "/api/v1/corpora/{c}/golden-sets", {"name": "Basics"}),
    ("POST", "/api/v1/corpora/{c}/golden-sets/from-template", {"template": "clinic"}),
    ("GET", f"/api/v1/corpora/{{c}}/golden-sets/{X}", None),
    ("PATCH", f"/api/v1/corpora/{{c}}/golden-sets/{X}", {"name": "Renamed"}),
    ("DELETE", f"/api/v1/corpora/{{c}}/golden-sets/{X}", None),
    ("POST", f"/api/v1/corpora/{{c}}/golden-sets/{X}/pairs", {"pairs": []}),
    ("GET", "/api/v1/corpora/{c}/eval/criteria", None),
    ("POST", "/api/v1/corpora/{c}/eval/criteria", {"name": "fee", "definition": "States the fee"}),
    ("PATCH", f"/api/v1/corpora/{{c}}/eval/criteria/{X}", {"enabled": False}),
    ("DELETE", f"/api/v1/corpora/{{c}}/eval/criteria/{X}", None),
    ("POST", "/api/v1/corpora/{c}/eval/runs", {"set_id": str(X), "mode": "fast"}),
    ("GET", "/api/v1/corpora/{c}/eval/runs", None),
    ("GET", f"/api/v1/corpora/{{c}}/eval/runs/{X}", None),
    ("GET", f"/api/v1/corpora/{{c}}/eval/runs/{X}/samples", None),
    ("PATCH", f"/api/v1/corpora/{{c}}/eval/samples/{X}", {"rating": "good"}),
    ("POST", "/api/v1/corpora/{c}/eval/compare", {"run_ids": [str(X), str(uuid.uuid4())]}),
    ("GET", "/api/v1/corpora/{c}/assistant/analytics", None),
    ("GET", "/api/v1/corpora/{c}/assistant/review", None),
    (
        "POST",
        "/api/v1/corpora/{c}/assistant/review/actions",
        {"item_key": "k" * 32, "action": "dismissed"},
    ),
    ("GET", "/api/v1/corpora/{c}/eval/settings", None),
    ("PUT", "/api/v1/corpora/{c}/eval/settings", {"judge_model": "judge-x"}),
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
