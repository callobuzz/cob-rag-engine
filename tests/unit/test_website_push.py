"""The push link (BIZ2): a learn key, the public endpoint, its limits and the log mask."""

import hashlib
import logging
import re
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.core.logging import LearnKeyRedactor
from callobuzz_rag.db.models.website import WebsiteSettings
from callobuzz_rag.website import push
from tests.unit.accounts_fakes import FakeSession
from tests.unit.test_auth_routes import PASSWORD, World, auth_app, login, req
from tests.unit.test_website_api import (
    SITE,
    _refresh_with_server_defaults,
    add_key,
    add_page,
    add_source,
    api,
    audits,
    make_corpus,
)

pytestmark = pytest.mark.usefixtures("unlimited_plan")

ACCESS_LINE = '%s - "%s %s HTTP/%s" %d'


@contextmanager
def app_for(world: World, **settings: Any):  # type: ignore[no-untyped-def]
    with (
        auth_app(world, **settings) as app,
        patch.object(FakeSession, "refresh", _refresh_with_server_defaults),
    ):
        app.state.vector_store = AsyncMock()
        app.state.website_redis = world.redis
        yield app


@pytest.fixture
def queued():
    with patch.object(push, "dispatch_urls", MagicMock(return_value=True)) as spy:
        yield spy


def setup(world: World) -> Any:
    corpus = make_corpus(world)
    add_key(world, corpus, [SITE])
    source = add_source(world, corpus, status="ready")
    return corpus, source


async def new_key(app: Any, corpus: Any) -> str:
    res = await api(app, "POST", corpus, "/learn-key")
    assert res.status_code == 201, res.text
    return str(res.json()["key"])


def post(app: Any, key: str, body: Any) -> Any:
    return req(app, "POST", f"/api/v1/public/learn/{key}", json=body)


async def test_push_accepts_in_scope_urls_and_rejects_the_rest(queued: Any) -> None:
    world = World()
    corpus, source = setup(world)
    with app_for(world) as app:
        key = await new_key(app, corpus)
        res = await post(
            app,
            key,
            {
                "urlList": [
                    "https://shreedental.in/pricing",
                    "https://www.shreedental.in/new-offer",
                    "https://evil.example/x",
                    "https://shreedental.in/cart",
                ]
            },
        )
    assert res.status_code == 202, res.text
    assert res.json() == {"accepted": 2, "rejected": 2}
    queued.assert_called_once_with(
        source.id,
        ["https://shreedental.in/pricing", "https://www.shreedental.in/new-offer"],
        "push",
        "push",
    )


async def test_query_strings_are_dropped_and_owner_exclusions_refused(queued: Any) -> None:
    world = World()
    corpus, source = setup(world)
    add_page(world, source, "/private", status="excluded", excluded_by_owner=True)
    world.db.add(
        WebsiteSettings(
            corpus_id=corpus.id, tenant_id=corpus.tenant_id, exclude_patterns=["/blog/*"]
        )
    )
    with app_for(world) as app:
        key = await new_key(app, corpus)
        res = await post(
            app,
            key,
            {
                "urlList": [
                    "https://shreedental.in/pricing?utm=1",
                    "https://shreedental.in/private",
                    "https://shreedental.in/blog/post",
                ]
            },
        )
    assert res.json() == {"accepted": 1, "rejected": 2}
    queued.assert_called_once_with(source.id, ["https://shreedental.in/pricing"], "push", "push")


async def test_a_paused_site_takes_no_pushes(queued: Any) -> None:
    world = World()
    corpus, source = setup(world)
    source.status = "paused"
    with app_for(world) as app:
        key = await new_key(app, corpus)
        res = await post(app, key, {"url": "https://shreedental.in/pricing"})
    assert res.json() == {"accepted": 0, "rejected": 1}
    queued.assert_not_called()


async def test_unknown_and_replaced_keys_answer_the_same(queued: Any) -> None:
    world = World()
    corpus, _source = setup(world)
    with app_for(world) as app:
        old = await new_key(app, corpus)
        await new_key(app, corpus)  # rotates: the old key stops working
        body = {"url": "https://shreedental.in/"}
        unknown = await post(app, "lk_" + "x" * 32, body)
        replaced = await post(app, old, body)
        malformed = await post(app, "lk_short", body)
    assert unknown.status_code == replaced.status_code == malformed.status_code == 404
    assert unknown.json() == replaced.json() == malformed.json()
    assert unknown.json()["code"] == "learn_key_invalid"
    queued.assert_not_called()


async def test_the_key_in_the_body_must_be_the_key_in_the_address(queued: Any) -> None:
    world = World()
    corpus, _source = setup(world)
    with app_for(world) as app:
        key = await new_key(app, corpus)
        other = await post(app, key, {"url": "https://shreedental.in/", "key": "lk_" + "y" * 32})
        same = await post(
            app, key, {"url": "https://shreedental.in/", "key": key, "host": "shreedental.in"}
        )
    assert other.status_code == 422 and other.json()["code"] == "learn_key_mismatch"
    assert same.status_code == 202


async def test_an_indexnow_client_key_is_ignored(queued: Any) -> None:
    world = World()
    corpus, _source = setup(world)
    with app_for(world) as app:
        key = await new_key(app, corpus)
        res = await post(
            app,
            key,
            {
                "host": "shreedental.in",
                "key": "a1b2c3d4e5f6a7b8c9d0",  # the client's own IndexNow key, never ours
                "keyLocation": "https://shreedental.in/a1b2c3d4e5f6a7b8c9d0.txt",
                "urlList": ["https://shreedental.in/pricing"],
            },
        )
    assert res.status_code == 202 and res.json() == {"accepted": 1, "rejected": 0}


async def test_a_body_needs_a_url_and_nothing_else(queued: Any) -> None:
    world = World()
    corpus, _source = setup(world)
    many = [f"https://shreedental.in/p{i}" for i in range(101)]
    with app_for(world) as app:
        key = await new_key(app, corpus)
        empty = await post(app, key, {"host": "shreedental.in"})
        extra = await post(app, key, {"url": "https://shreedental.in/", "text": "<p>hi</p>"})
        too_many = await post(app, key, {"urlList": many})
    assert empty.status_code == extra.status_code == too_many.status_code == 422
    queued.assert_not_called()


async def test_the_same_url_twice_within_a_minute_is_queued_once(queued: Any) -> None:
    world = World()
    corpus, source = setup(world)
    body = {"url": "https://shreedental.in/pricing"}
    with app_for(world) as app:
        key = await new_key(app, corpus)
        first = await post(app, key, body)
        second = await post(app, key, body)
        assert queued.call_count == 1
        world.redis.advance(61)
        third = await post(app, key, body)
    assert first.json() == second.json() == third.json() == {"accepted": 1, "rejected": 0}
    assert queued.call_count == 2
    assert queued.call_args.args[0] == source.id


async def test_the_minute_limit_refuses_the_whole_request(queued: Any) -> None:
    world = World()
    corpus, _source = setup(world)
    urls = [f"https://shreedental.in/p{i}" for i in range(4)]
    with app_for(world, website_push_per_minute=3) as app:
        key = await new_key(app, corpus)
        res = await post(app, key, {"urlList": urls})
    assert res.status_code == 429 and res.json()["code"] == "learn_rate_limited"
    assert 1 <= int(res.headers["retry-after"]) <= 60
    queued.assert_not_called()


async def test_the_day_limit_also_refuses(queued: Any) -> None:
    world = World()
    corpus, _source = setup(world)
    with app_for(world, website_push_per_minute=100, website_push_per_day=2) as app:
        key = await new_key(app, corpus)
        ok = await post(app, key, {"url": "https://shreedental.in/a"})
        ok2 = await post(app, key, {"url": "https://shreedental.in/b"})
        over = await post(app, key, {"url": "https://shreedental.in/c"})
    assert ok.status_code == ok2.status_code == 202
    assert over.status_code == 429 and over.json()["code"] == "learn_rate_limited"


async def test_redis_down_fails_closed(queued: Any) -> None:
    world = World()
    corpus, _source = setup(world)
    with app_for(world) as app:
        key = await new_key(app, corpus)
        world.redis.down = True
        res = await post(app, key, {"url": "https://shreedental.in/pricing"})
    assert res.status_code == 503 and res.json()["code"] == "learn_unavailable"
    queued.assert_not_called()


async def test_a_queue_that_is_down_is_a_503() -> None:
    world = World()
    corpus, _source = setup(world)
    with (
        app_for(world) as app,
        patch.object(push, "dispatch_urls", MagicMock(return_value=False)),
    ):
        key = await new_key(app, corpus)
        res = await post(app, key, {"url": "https://shreedental.in/pricing"})
    assert res.status_code == 503 and res.json()["code"] == "learn_unavailable"


async def test_a_queue_that_fails_once_does_not_eat_the_retry() -> None:
    world = World()
    corpus, source = setup(world)
    body = {"url": "https://shreedental.in/pricing"}
    with (
        app_for(world) as app,
        patch.object(push, "dispatch_urls", MagicMock(side_effect=[False, True])) as spy,
    ):
        key = await new_key(app, corpus)
        refused = await post(app, key, body)
        retry = await post(app, key, body)
    assert refused.status_code == 503
    assert retry.status_code == 202 and retry.json() == {"accepted": 1, "rejected": 0}
    assert spy.call_count == 2 and spy.call_args.args[0] == source.id


async def test_a_client_address_is_limited_before_any_key_is_looked_at(queued: Any) -> None:
    from types import SimpleNamespace

    world = World()
    setup(world)
    unknown = "lk_" + "z" * 32
    body = {"url": "https://shreedental.in/pricing"}
    with (
        app_for(world) as app,
        patch.object(push, "IP_PER_MINUTE", 5),
        patch.object(push, "time", SimpleNamespace(time=lambda: 1_000_000.0)),
    ):
        first = [await post(app, unknown, body) for _ in range(5)]
        over = await post(app, unknown, body)
    assert [r.status_code for r in first] == [404] * 5
    assert over.status_code == 429 and over.json()["code"] == "learn_rate_limited"
    assert 1 <= int(over.headers["retry-after"]) <= 60
    queued.assert_not_called()


async def test_redis_down_does_not_block_the_address_limit_check(queued: Any) -> None:
    world = World()
    setup(world)
    with app_for(world) as app:
        world.redis.down = True
        res = await post(app, "lk_" + "z" * 32, {"url": "https://shreedental.in/pricing"})
    assert res.status_code == 404  # the key lookup still answers; the limits fail open here


async def test_making_a_link_is_refused_when_learning_is_off() -> None:
    world = World()
    corpus, _source = setup(world)
    world.db.default_plan.website_learning_enabled = False
    with app_for(world) as app:
        res = await api(app, "POST", corpus, "/learn-key")
    assert res.status_code == 409 and res.json()["code"] == "website_learning_disabled"
    settings = [r for r in world.db.all(WebsiteSettings) if r.corpus_id == corpus.id]
    assert all(r.learn_key_hash is None for r in settings)


async def test_learning_switched_off_answers_409(queued: Any) -> None:
    world = World()
    corpus, _source = setup(world)
    with app_for(world) as app:
        key = await new_key(app, corpus)
        world.db.default_plan.website_learning_enabled = False
        res = await post(app, key, {"url": "https://shreedental.in/pricing"})
    assert res.status_code == 409 and res.json()["code"] == "website_learning_disabled"
    queued.assert_not_called()


async def test_a_suspended_workspace_looks_like_an_unknown_key(queued: Any) -> None:
    world = World()
    corpus, _source = setup(world)
    with app_for(world) as app:
        key = await new_key(app, corpus)
        world.shree.status = "suspended"
        res = await post(app, key, {"url": "https://shreedental.in/pricing"})
    assert res.status_code == 404 and res.json()["code"] == "learn_key_invalid"


async def test_the_get_form_takes_one_url(queued: Any) -> None:
    world = World()
    corpus, source = setup(world)
    with app_for(world) as app:
        key = await new_key(app, corpus)
        res = await req(
            app,
            "GET",
            f"/api/v1/public/learn/{key}",
            params={"url": "https://shreedental.in/offers"},
        )
        none = await req(app, "GET", f"/api/v1/public/learn/{key}")
    assert res.status_code == 202 and res.json() == {"accepted": 1, "rejected": 0}
    queued.assert_called_once_with(source.id, ["https://shreedental.in/offers"], "push", "push")
    assert none.status_code == 422


async def test_the_key_is_stored_hashed_and_shown_once(queued: Any) -> None:
    world = World()
    corpus, _source = setup(world)
    with app_for(world) as app:
        created = await api(app, "POST", corpus, "/learn-key")
        overview = await api(app, "GET", corpus)
    body = created.json()
    key = body["key"]
    assert re.fullmatch(r"lk_[A-Za-z0-9_-]{32}", key) and body["hint"] == key[-4:]
    assert body["push_url"].endswith(f"/api/v1/public/learn/{key}")
    row = world.db.all(WebsiteSettings)[0]
    assert row.learn_key_hash == hashlib.sha256(key.encode()).hexdigest()
    assert row.learn_key_hint == key[-4:]
    assert key not in overview.text and row.learn_key_hash not in overview.text
    assert overview.json()["learn_key"]["set"] is True
    assert overview.json()["learn_key"]["hint"] == key[-4:]


async def test_a_new_key_replaces_the_old_and_remove_stops_it(queued: Any) -> None:
    world = World()
    corpus, _source = setup(world)
    with app_for(world) as app:
        first = await new_key(app, corpus)
        second = await new_key(app, corpus)
        gone = await api(app, "DELETE", corpus, "/learn-key")
        after = await post(app, second, {"url": "https://shreedental.in/"})
        overview = await api(app, "GET", corpus)
    assert first != second and gone.status_code == 204
    assert after.status_code == 404
    assert overview.json()["learn_key"] == {"set": False, "hint": None, "created_at": None}


async def test_making_and_removing_the_link_is_for_owners_and_audited(queued: Any) -> None:
    world = World()
    corpus, _source = setup(world)
    member = world.user("ravi@shree.example")
    world.member(member, world.shree, "member")
    with app_for(world) as app:
        token = (await login(app, "ravi@shree.example", PASSWORD)).json()["access_token"]
        base = f"/api/v1/corpora/{corpus.id}/website/learn-key"
        for method in ("POST", "DELETE"):
            res = await req(app, method, base, token=token)
            assert res.status_code == 403 and res.json()["code"] == "owner_required"
        key = await new_key(app, corpus)
        await api(app, "DELETE", corpus, "/learn-key")
    (created,) = audits(world, "website.learn_key_created")
    (revoked,) = audits(world, "website.learn_key_revoked")
    assert created.details["hint"] == key[-4:] and revoked.details["hint"] == key[-4:]
    for entry in (created, revoked):
        assert key not in str(entry.details) and "key" not in entry.details


def _record(path: str) -> logging.LogRecord:
    args = ("1.2.3.4:5", "POST", path, "1.1", 202)
    return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, ACCESS_LINE, args, None)


def test_access_log_masks_the_key() -> None:
    record = _record("/api/v1/public/learn/lk_abcdefghijklmnopqrstuvwxyz012345")
    assert LearnKeyRedactor().filter(record) is True
    assert record.args is not None
    assert record.args[2] == "/api/v1/public/learn/lk_…2345"
    assert "abcdefgh" not in record.getMessage()


def test_the_mask_leaves_other_paths_alone_and_keeps_a_query_string() -> None:
    other = _record("/api/v1/widget/config?x=1")
    LearnKeyRedactor().filter(other)
    assert other.args is not None and other.args[2] == "/api/v1/widget/config?x=1"
    with_url = _record(
        "/api/v1/public/learn/lk_abcdefghijklmnopqrstuvwxyz012345?url=https://a.in/x"
    )
    LearnKeyRedactor().filter(with_url)
    assert with_url.args is not None
    assert with_url.args[2] == "/api/v1/public/learn/lk_…2345?url=https://a.in/x"


def test_configure_logging_adds_the_mask_once() -> None:
    from callobuzz_rag.core.logging import configure_logging

    configure_logging("WARNING")
    configure_logging("WARNING")
    access = logging.getLogger("uvicorn.access")
    assert sum(isinstance(f, LearnKeyRedactor) for f in access.filters) == 1
