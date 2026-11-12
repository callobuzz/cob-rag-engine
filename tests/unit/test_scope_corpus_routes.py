"""Documents, self-service config, conversations and memories take every key kind (G-4).

They used to accept only a corpus key, so an admin or tenant dashboard would have
needed each client's ck_ key, which cannot be read back. Now admin and tenant keys
name the corpus (`?corpus_id=` or the path), scoped like every other route.
"""

import uuid

import pytest

from tests.unit.scope_harness import MATRIX, call, check, expected, scoped_app

X = uuid.uuid4()  # an id that exists nowhere

ROUTES = [
    ("GET", "/api/v1/documents?corpus_id={c}", None),
    ("GET", f"/api/v1/documents/{X}/versions?corpus_id={{c}}", None),
    ("DELETE", f"/api/v1/documents/{X}?corpus_id={{c}}", None),
    ("POST", f"/api/v1/documents/{X}/replace/{uuid.uuid4()}?corpus_id={{c}}", None),
    ("POST", f"/api/v1/documents/{X}/versions/{X}/restore?corpus_id={{c}}", None),
    ("GET", "/api/v1/config?corpus_id={c}", None),
    ("PATCH", "/api/v1/config?corpus_id={c}", {"prompt_config": {"system_prompt": "Hi"}}),
    ("GET", "/api/v1/corpora/{c}/conversations", None),
    ("GET", f"/api/v1/corpora/{{c}}/conversations/{X}", None),
    ("GET", f"/api/v1/corpora/{{c}}/conversations/{X}/messages", None),
    ("DELETE", f"/api/v1/corpora/{{c}}/conversations/{X}", None),
    ("GET", "/api/v1/corpora/{c}/memories", None),
    ("GET", f"/api/v1/corpora/{{c}}/memories/{X}", None),
    ("DELETE", f"/api/v1/corpora/{{c}}/memories/{X}", None),
    ("DELETE", "/api/v1/corpora/{c}/memories?user_id=u", None),
    ("GET", "/api/v1/handoffs?corpus_id={c}", None),
]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
@pytest.mark.parametrize(("key", "which", "outcome"), MATRIX)
async def test_scoping(method, path, body, key, which, outcome) -> None:
    with scoped_app() as (app, world):
        url = path.format(c=world.corpus(which).id)
        res = await call(app, key, method, url, json=body)
    check(expected(key, outcome, method, url, body), res.status_code, res.text)


@pytest.mark.parametrize("path", ["/api/v1/documents", "/api/v1/config"])
async def test_corpus_key_needs_no_corpus_id(path) -> None:
    with scoped_app() as (app, _):
        res = await call(app, "ck_a", "GET", path)
    assert res.status_code == 200, res.text


@pytest.mark.parametrize("key", ["admin", "tk_a"])
@pytest.mark.parametrize("path", ["/api/v1/documents", "/api/v1/config"])
async def test_admin_and_tenant_must_name_the_corpus(key, path) -> None:
    with scoped_app() as (app, _):
        res = await call(app, key, "GET", path)
    assert res.status_code == 422
    assert "corpus_id" in res.json()["detail"]


@pytest.mark.parametrize("key", ["admin", "tk_a"])
async def test_unknown_corpus_is_404(key) -> None:
    with scoped_app() as (app, _):
        res = await call(app, key, "GET", f"/api/v1/config?corpus_id={X}")
    assert res.status_code == 404


async def test_config_view_is_the_same_for_every_key() -> None:
    with scoped_app() as (app, world):
        world.corpus_a.retrieval_config = {"top_k": 12}
        world.corpus_a.llm_config = {"model": "m", "api_key_encrypted": "secret"}
        seen = []
        for key in ("admin", "tk_a", "ck_a"):
            res = await call(app, key, "GET", f"/api/v1/config?corpus_id={world.corpus_a.id}")
            assert res.status_code == 200
            seen.append(res.json())
    assert seen[0] == seen[1] == seen[2]
    assert seen[0]["corpus_id"] == str(world.corpus_a.id)
    assert seen[0]["llm_config"]["api_key_encrypted"] == "***"


@pytest.mark.parametrize("key", ["admin", "tk_a"])
async def test_config_patch_applies_the_same_bounds(key) -> None:
    with scoped_app() as (app, world):
        res = await call(
            app,
            key,
            "PATCH",
            f"/api/v1/config?corpus_id={world.corpus_a.id}",
            json={"retrieval_config": {"top_k": 1000}},
        )
        assert res.status_code == 422, res.text  # top_k is 1-100 for every key
        ok = await call(
            app,
            key,
            "PATCH",
            f"/api/v1/config?corpus_id={world.corpus_a.id}",
            json={"retrieval_config": {"top_k": 30}},
        )
    assert ok.status_code == 200, ok.text
    assert world.corpus_a.retrieval_config == {"top_k": 30}
