"""The widget's freshness sensor, server side (BIZ2): what it accepts, what it queues, what it
never reveals. Runs the real widget authorisation on the in-memory database and a fake Redis."""

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.db.models.website import WebsitePage, WebsiteSettings
from callobuzz_rag.website import push
from callobuzz_rag.website.urls import url_hash
from callobuzz_rag.widget.security import LimitResult
from tests.unit.accounts_fakes import FakeSession
from tests.unit.test_auth_routes import World, auth_app, req
from tests.unit.test_website_api import (
    SITE,
    _refresh_with_server_defaults,
    add_key,
    add_source,
    make_corpus,
)

pytestmark = pytest.mark.usefixtures("unlimited_plan")

FP_A, FP_B, FP_C = "a" * 16, "b" * 16, "c" * 16
PAGE = f"{SITE}/pricing"


@contextmanager
def app_for(world: World, **settings: Any):  # type: ignore[no-untyped-def]
    with (
        auth_app(world, **settings) as app,
        patch.object(FakeSession, "refresh", _refresh_with_server_defaults),
    ):
        app.state.vector_store = AsyncMock()
        app.state.website_redis = world.redis
        app.state.widget_rate_limiter = SimpleNamespace(
            check_event=AsyncMock(return_value=LimitResult(True))
        )
        yield app


@pytest.fixture
def queued():
    with patch.object(push, "dispatch_urls", MagicMock(return_value=True)) as spy:
        yield spy


def add_page(world: World, source: Any, path: str, **kw: Any) -> Any:
    url = f"{SITE}{path}"
    fields = {"status": "learned", **kw}
    return world.db.add(
        WebsitePage(
            source_id=source.id,
            corpus_id=source.corpus_id,
            url=url,
            url_hash=url_hash(url),
            **fields,
        )
    )


def setup(world: World, *, learned: bool = True) -> Any:
    corpus = make_corpus(world)
    key = add_key(world, corpus, [SITE])
    source = add_source(world, corpus, status="ready")
    page = add_page(world, source, "/pricing") if learned else None
    return corpus, key, source, page


def report(app: Any, key: Any, url: str = PAGE, fingerprint: str = FP_A, **kw: Any) -> Any:
    headers = {"X-Widget-Key": key.public_key, "Origin": SITE, **kw.pop("headers", {})}
    body = kw.pop("json", {"page_url": url, "fingerprint": fingerprint})
    return req(app, "POST", "/api/v1/widget/page", headers=headers, json=body, **kw)


async def test_the_first_report_is_stored_and_a_repeat_is_nothing(queued: Any) -> None:
    world = World()
    _corpus, key, _source, page = setup(world)
    with app_for(world) as app:
        first = await report(app, key)
        same = await report(app, key)
    assert first.status_code == same.status_code == 204 and first.content == b""
    assert page.sensor_fingerprint == FP_A and page.sensor_seen_at is not None
    queued.assert_not_called()


async def test_a_changed_code_asks_for_one_server_read(queued: Any) -> None:
    world = World()
    _corpus, key, source, page = setup(world)
    with app_for(world, website_sensor_min_interval_seconds=1800) as app:
        await report(app, key, fingerprint=FP_A)
        changed = await report(app, key, fingerprint=FP_B)
        third = await report(app, key, fingerprint=FP_C)  # inside the minimum interval
    assert changed.status_code == third.status_code == 204
    queued.assert_called_once_with(source.id, [PAGE], "sensor", "sensor")
    # the throttled report did not overwrite the code: the next one let through still differs
    assert page.sensor_fingerprint == FP_B and page.sensor_seen_at is not None


async def test_a_throttled_change_is_not_lost(queued: Any) -> None:
    world = World()
    _corpus, key, _source, page = setup(world)
    with app_for(world, website_sensor_min_interval_seconds=1800) as app:
        await report(app, key, fingerprint=FP_A)
        await report(app, key, fingerprint=FP_B)  # queued
        await report(app, key, fingerprint=FP_C)  # throttled: the signal must survive
        world.redis.advance(1801)
        await report(app, key, fingerprint=FP_C)  # the same code again, now let through
    assert queued.call_count == 2 and page.sensor_fingerprint == FP_C


async def test_after_the_interval_a_change_is_queued_again(queued: Any) -> None:
    world = World()
    _corpus, key, _source, _page = setup(world)
    with app_for(world, website_sensor_min_interval_seconds=1800) as app:
        await report(app, key, fingerprint=FP_A)
        await report(app, key, fingerprint=FP_B)
        world.redis.advance(1801)
        await report(app, key, fingerprint=FP_C)
    assert queued.call_count == 2


async def test_a_page_of_another_origin_is_ignored(queued: Any) -> None:
    world = World()
    _corpus, key, _source, page = setup(world)
    with app_for(world) as app:
        other = await report(app, key, url="https://evil.example/pricing")
        www = await report(app, key, url="https://www.shreedental.in/pricing")  # not the Origin
    assert other.status_code == www.status_code == 204
    assert page.sensor_fingerprint is None
    queued.assert_not_called()


async def test_a_wrong_origin_for_the_key_is_refused_like_every_widget_route(queued: Any) -> None:
    world = World()
    _corpus, key, _source, _page = setup(world)
    with app_for(world) as app:
        res = await report(app, key, headers={"Origin": "https://evil.example"})
    assert res.status_code == 403
    queued.assert_not_called()


async def test_private_pages_query_variants_and_exclusions_are_ignored(queued: Any) -> None:
    world = World()
    corpus, key, source, page = setup(world)
    world.db.add(
        WebsiteSettings(
            corpus_id=corpus.id, tenant_id=corpus.tenant_id, exclude_patterns=["/blog/*"]
        )
    )
    left_out = add_page(world, source, "/offers", status="excluded", excluded_by_owner=True)
    with app_for(world) as app:
        for url in (
            f"{SITE}/cart",
            f"{SITE}/pricing?utm=1",
            f"{SITE}/blog/new-clinic",
            f"{SITE}/offers",
        ):
            for fp in (FP_A, FP_B):
                res = await report(app, key, url=url, fingerprint=fp)
                assert res.status_code == 204, url
    assert page.sensor_fingerprint is None and left_out.sensor_fingerprint is None
    queued.assert_not_called()


async def test_extra_fields_and_a_bad_code_are_refused(queued: Any) -> None:
    world = World()
    _corpus, key, _source, _page = setup(world)
    with app_for(world) as app:
        text = await report(
            app, key, json={"page_url": PAGE, "fingerprint": FP_A, "text": "Root canal Rs 4500"}
        )
        short = await report(app, key, fingerprint="abc")
        upper = await report(app, key, fingerprint="A" * 16)
    assert text.status_code == short.status_code == upper.status_code == 422
    queued.assert_not_called()


async def test_an_unknown_address_is_never_learned_from_a_report(queued: Any) -> None:
    world = World()
    _corpus, key, _source, _page = setup(world, learned=False)
    with app_for(world) as app:
        first = await report(app, key, url=f"{SITE}/x1")
        second = await report(app, key, url=f"{SITE}/x2", fingerprint=FP_B)
    assert first.status_code == second.status_code == 204
    queued.assert_not_called()  # a forged Origin could otherwise add a page per made-up path


async def test_a_known_page_that_is_not_learned_is_one_discovery_hint(queued: Any) -> None:
    world = World()
    _corpus, key, source, _page = setup(world, learned=False)
    add_page(world, source, "/new-treatment", status="failed")
    with app_for(world) as app:
        first = await report(app, key, url=f"{SITE}/new-treatment")
        again = await report(app, key, url=f"{SITE}/new-treatment", fingerprint=FP_B)
    assert first.status_code == again.status_code == 204
    queued.assert_called_once_with(source.id, [f"{SITE}/new-treatment"], "sensor", "sensor")


async def test_a_known_page_over_the_cap_is_ignored(queued: Any) -> None:
    world = World()
    world.db.default_plan.website_pages_max = 2
    _corpus, key, source, _page = setup(world, learned=False)
    add_page(world, source, "/new-treatment", status="failed")
    source.pages_learned = 2
    with app_for(world) as app:
        res = await report(app, key, url=f"{SITE}/new-treatment")
    assert res.status_code == 204
    queued.assert_not_called()


@pytest.mark.parametrize("off", ["engine", "plan", "kb"])
async def test_learning_off_means_204_and_nothing(queued: Any, off: str) -> None:
    world = World()
    corpus, key, _source, page = setup(world)
    settings: dict[str, Any] = {}
    if off == "engine":
        settings["website_learning_enabled"] = False
    elif off == "plan":
        world.db.default_plan.website_learning_enabled = False
    else:
        world.db.add(
            WebsiteSettings(corpus_id=corpus.id, tenant_id=corpus.tenant_id, enabled=False)
        )
    with app_for(world, **settings) as app:
        await report(app, key, fingerprint=FP_A)
        res = await report(app, key, fingerprint=FP_B)
    assert res.status_code == 204 and page.sensor_fingerprint is None
    queued.assert_not_called()


async def test_a_paused_site_is_ignored(queued: Any) -> None:
    world = World()
    _corpus, key, source, page = setup(world)
    source.status = "paused"
    with app_for(world) as app:
        res = await report(app, key)
    assert res.status_code == 204 and page.sensor_fingerprint is None
    queued.assert_not_called()


async def test_at_most_sixty_sensor_reads_per_site_per_hour(queued: Any) -> None:
    world = World()
    corpus, key, source, _page = setup(world)
    for i in range(70):
        add_page(world, source, f"/p{i}", sensor_fingerprint=FP_A)
    with app_for(world) as app:
        for i in range(70):
            await report(app, key, url=f"{SITE}/p{i}", fingerprint=FP_B)
    assert queued.call_count == 60
    assert corpus.id  # the ceiling is per site, and the 61st change simply is not queued
    held_back = [p for p in world.db.all(WebsitePage) if p.sensor_fingerprint == FP_A]
    assert len(held_back) == 10  # their signal is kept for the next report


async def test_redis_down_is_still_204_and_queues_nothing(queued: Any) -> None:
    world = World()
    _corpus, key, _source, _page = setup(world)
    with app_for(world) as app:
        await report(app, key, fingerprint=FP_A)
        world.redis.down = True
        res = await report(app, key, fingerprint=FP_B)
    assert res.status_code == 204
    queued.assert_not_called()


async def test_a_rate_limited_visitor_is_still_told_nothing(queued: Any) -> None:
    world = World()
    _corpus, key, _source, page = setup(world)
    with app_for(world) as app:
        app.state.widget_rate_limiter.check_event = AsyncMock(
            return_value=LimitResult(False, 30, "slow down")
        )
        res = await report(app, key)
    assert res.status_code == 204 and page.sensor_fingerprint is None


async def test_a_suspended_workspace_gets_the_usual_widget_refusal(queued: Any) -> None:
    world = World()
    _corpus, key, _source, _page = setup(world)
    world.shree.status = "suspended"
    with app_for(world) as app:
        res = await report(app, key)
    assert res.status_code == 403
    queued.assert_not_called()


# ---------------------------------------------------------------- GET /widget/config


def config(app: Any, key: Any, origin: str = SITE) -> Any:
    return req(
        app,
        "GET",
        "/api/v1/widget/config",
        headers={"X-Widget-Key": key.public_key, "Origin": origin},
    )


async def test_config_carries_the_sensor_block_when_learning_is_on() -> None:
    world = World()
    corpus, key, _source, _page = setup(world)
    world.db.add(
        WebsiteSettings(
            corpus_id=corpus.id, tenant_id=corpus.tenant_id, exclude_patterns=["/blog/*"]
        )
    )
    with app_for(world, website_sensor_sample=0.25) as app:
        res = await config(app, key)
    assert res.status_code == 200, res.text
    sensor = res.json()["sensor"]
    assert sensor["sample"] == 0.25
    assert "/login" in sensor["skip"] and "/cart" in sensor["skip"] and "/blog/*" in sensor["skip"]


@pytest.mark.parametrize("case", ["engine", "plan", "no_site", "paused", "sample_zero"])
async def test_config_has_no_sensor_otherwise(case: str) -> None:
    world = World()
    corpus, key, source, _page = setup(world)
    settings: dict[str, Any] = {}
    if case == "engine":
        settings["website_learning_enabled"] = False
    elif case == "plan":
        world.db.default_plan.website_learning_enabled = False
    elif case == "no_site":
        world.db.rows[type(source)].clear()
    elif case == "paused":
        source.status = "paused"
    else:
        settings["website_sensor_sample"] = 0
    with app_for(world, **settings) as app:
        res = await config(app, key)
    assert res.status_code == 200, res.text
    assert res.json()["sensor"] is None and corpus.id


async def test_widget_js_carries_the_sensor_in_front_of_the_widget() -> None:
    world = World()
    with app_for(world) as app:
        res = await req(app, "GET", "/widget.js")
    assert res.status_code == 200
    body = res.text
    assert "window.CobSensor" in body
    assert body.index("window.CobSensor = ") < body.index("attachShadow")
    assert "window.CobSensor.run(API, KEY, cfg.sensor)" in body


def test_unused_names() -> None:
    assert WebsitePage
