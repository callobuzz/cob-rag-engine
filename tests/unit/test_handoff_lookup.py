"""TV2: GET /api/v1/handoffs/lookup finds a conversation by the reference a visitor quoted."""

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

from tests.unit.scope_harness import call, scoped_app

CONV = uuid.uuid4()
HANDOFF = uuid.uuid4()


def _found(corpus_id: uuid.UUID) -> list[dict[str, Any]]:
    return [
        {
            "corpus_id": corpus_id,
            "conversation_id": CONV,
            "handoff_ids": [HANDOFF],
            "lead_ids": [],
        }
    ]


def _patched(allowed: Any, matches: Any) -> Any:
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(
        patch(
            "callobuzz_rag.api.routes.handoffs.authorized_corpus_ids",
            AsyncMock(return_value=allowed),
        )
    )
    find = stack.enter_context(
        patch("callobuzz_rag.api.routes.handoffs.find_matches", AsyncMock(return_value=matches))
    )
    stack.find = find  # type: ignore[attr-defined]
    return stack


async def test_lookup_finds_the_conversation_and_its_handoffs() -> None:
    with scoped_app() as (app, world):
        with _patched([world.corpus_a.id], _found(world.corpus_a.id)) as p:
            r = await call(
                app, "owner_a", "GET", "/api/v1/handoffs/lookup", params={"ref": "sd4f7k"}
            )
        assert p.find.await_args.args[1:] == ("SD-4F7K", [world.corpus_a.id])
    assert r.status_code == 200, r.text
    assert r.json() == {
        "ref": "SD-4F7K",
        "matches": [
            {
                "corpus_id": str(world.corpus_a.id),
                "conversation_id": str(CONV),
                "handoff_ids": [str(HANDOFF)],
                "lead_ids": [],
            }
        ],
    }


async def test_a_reference_nobody_in_scope_holds_is_not_found() -> None:
    with scoped_app() as (app, world), _patched([world.corpus_a.id], []):
        r = await call(app, "owner_a", "GET", "/api/v1/handoffs/lookup", params={"ref": "SD-4F7K"})
    assert r.status_code == 404
    assert r.json()["code"] == "reference_not_found"


async def test_a_bad_reference_is_422_and_never_searches() -> None:
    with scoped_app() as (app, world), _patched([world.corpus_a.id], []) as p:
        for bad in ("hello", "SD-4F7O", ""):
            r = await call(app, "owner_a", "GET", "/api/v1/handoffs/lookup", params={"ref": bad})
            assert r.status_code == 422, bad
            if bad:
                assert r.json()["code"] == "reference_invalid"
        p.find.assert_not_awaited()


async def test_a_corpus_key_searches_only_its_own_knowledge_base() -> None:
    with scoped_app() as (app, world), _patched([world.corpus_a.id], []) as p:
        r = await call(app, "ck_a", "GET", "/api/v1/handoffs/lookup", params={"ref": "SD-4F7K"})
        assert p.find.await_args.args[2] == [world.corpus_a.id]
    assert r.status_code == 404


async def test_a_member_session_can_look_up() -> None:
    with scoped_app() as (app, world), _patched([world.corpus_a.id], _found(world.corpus_a.id)):
        r = await call(app, "member_a", "GET", "/api/v1/handoffs/lookup", params={"ref": "SD-4F7K"})
    assert r.status_code == 200


async def test_the_route_is_not_shadowed_by_the_id_route() -> None:
    with scoped_app() as (app, world), _patched([world.corpus_a.id], _found(world.corpus_a.id)):
        r = await call(app, "owner_a", "GET", "/api/v1/handoffs/lookup", params={"ref": "SD-4F7K"})
    assert r.status_code != 422 or r.json().get("code") == "reference_invalid"
    assert "matches" in r.json()


async def test_no_key_no_lookup() -> None:
    with scoped_app() as (app, _world):
        r = await call(app, None, "GET", "/api/v1/handoffs/lookup", params={"ref": "SD-4F7K"})
    assert r.status_code == 401
