"""The Docs routes: session-only, role-filtered, free."""

import pytest

from tests.unit.scope_harness import call

ROUTES = ["/api/v1/docs/tree", "/api/v1/docs/pages/screens/widget", "/api/v1/docs/search?q=widget"]


async def test_owner_reads_tree_and_pages(cob) -> None:  # type: ignore[no-untyped-def]
    tree = (await cob.get("alice", "/api/v1/docs/tree")).json()
    slugs = {p["slug"] for t in tree["topics"] for p in t["pages"]}
    assert "screens/widget" in slugs and "screens/engine" not in slugs
    page = await cob.get("alice", "/api/v1/docs/pages/screens/widget")
    assert page.status_code == 200
    assert page.json()["slug"] == "screens/widget" and page.json()["toc"]


async def test_a_member_does_not_see_admin_sections(cob) -> None:  # type: ignore[no-untyped-def]
    res = await cob.get("ravi", "/api/v1/docs/pages/screens/overview")
    assert res.status_code == 200 and "Sign-ups waiting" not in res.json()["markdown"]
    res = await cob.get("sa", "/api/v1/docs/pages/screens/overview")
    assert "Sign-ups waiting" in res.json()["markdown"]


async def test_an_admin_page_is_404_for_an_owner(cob) -> None:  # type: ignore[no-untyped-def]
    for path in ("screens/engine", "nope/nothing"):
        res = await cob.get("alice", f"/api/v1/docs/pages/{path}")
        assert res.status_code == 404 and res.json()["code"] == "docs_page_not_found"
    assert (await cob.get("sa", "/api/v1/docs/pages/screens/engine")).status_code == 200


@pytest.mark.parametrize("key", ["admin", "tk_a", "ck_a"])
@pytest.mark.parametrize("path", ROUTES)
async def test_keys_are_refused(cob, key, path) -> None:  # type: ignore[no-untyped-def]
    res = await call(cob.app, key, "GET", path)
    assert res.status_code == 403 and res.json()["code"] == "session_required"


async def test_no_credential_is_401(cob) -> None:  # type: ignore[no-untyped-def]
    assert (await cob.get(None, "/api/v1/docs/tree")).status_code == 401


async def test_search_is_free_filtered_and_validated(cob) -> None:  # type: ignore[no-untyped-def]
    res = await cob.get("alice", "/api/v1/docs/search?q=website%20widget")
    assert res.status_code == 200
    items = res.json()["items"]
    assert items[0]["slug"] == "screens/widget"
    assert "screens/engine" not in [i["slug"] for i in items]
    assert cob.retriever.calls[-1]["metadata_filters"] == {"audience": ["customer", "public"]}
    assert cob.director.built == 0
    assert (await cob.get("alice", "/api/v1/docs/search?q=")).status_code == 422
    blank = await cob.get("alice", "/api/v1/docs/search?q=%20%20")
    assert blank.status_code == 422 and blank.json()["code"] == "docs_query_empty"
    assert (await cob.get("alice", "/api/v1/docs/search")).status_code == 422


async def test_search_failure_is_503(cob) -> None:  # type: ignore[no-untyped-def]
    async def boom(*a, **k):  # type: ignore[no-untyped-def]
        raise RuntimeError("qdrant down")

    cob.retriever.run_retrieval_pipeline = boom  # type: ignore[method-assign]
    res = await cob.get("alice", "/api/v1/docs/search?q=zzz")
    assert res.status_code == 503 and res.json()["code"] == "docs_search_unavailable"


async def test_the_docs_work_when_cob_is_switched_off(cob) -> None:  # type: ignore[no-untyped-def]
    cob.app.state.settings.cob_enabled = False
    assert (await cob.get("alice", "/api/v1/docs/tree")).status_code == 200
