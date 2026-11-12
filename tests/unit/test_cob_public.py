"""POST /api/v1/public/cob/chat: docs-only, public audience, nothing stored, two off switches."""

import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from callobuzz_rag.accounts.settings_store import invalidate_engine_settings_cache
from callobuzz_rag.cob.public import PublicCobLimiter
from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.engine_settings import EngineSettingsRow
from callobuzz_rag.db.models.message import Message
from tests.unit.accounts_fakes import FakeRedis
from tests.unit.cob_harness import ANSWER, CobClient, parse_sse, tokens
from tests.unit.scope_harness import call

PUBLIC = "/api/v1/public/cob/chat"


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    invalidate_engine_settings_cache()
    yield
    invalidate_engine_settings_cache()


@pytest.fixture
def pub(cob: CobClient) -> CobClient:
    cob.app.state.cob_public_limiter = PublicCobLimiter(FakeRedis(), per_ip_per_hour=50, daily=500)
    return cob


async def post(cob: CobClient, body: dict[str, Any], key: str | None = None) -> Any:
    return await call(cob.app, key, "POST", PUBLIC, json=body)


async def sse(cob: CobClient, body: dict[str, Any], key: str | None = None) -> list[Any]:
    res = await post(cob, body, key)
    assert res.status_code == 200, res.text
    return parse_sse(res.text)


async def test_public_turn_is_docs_only_and_public_audience(pub: CobClient) -> None:
    events = await sse(pub, {"message": "What is cob-rag-engine?"})
    assert pub.director.kwargs["retrieval_filters"] == {"audience": ["public"]}
    assert pub.director.kwargs["use_headers"] is False
    assert pub.director.kwargs["llm_config_override"] is None
    turn = pub.director.turn
    assert turn.usage_source == "cob_public" and turn.tenant_id == pub.world.system_tenant
    assert turn.corpus_id == pub.world.corpus.id
    assert turn.persist is False and turn.conversation_id is None
    names = [e.event for e in events]
    assert names[0] == "meta" and names[-1] == "done" and "token" in names
    assert events[0].data["role"] == "public" and events[0].data["intent"] == "docs"
    assert events[0].data["conversation_id"] is None
    assert events[-1].data["conversation_id"] is None
    assert tokens(events) == ANSWER
    assert not [e for e in events if e.event == "ui" and e.data["kind"] == "cob_action_card"]


async def test_nothing_is_stored(pub: CobClient) -> None:
    events = await sse(pub, {"message": "What is cob-rag-engine?"})
    assert pub.db.all(Conversation) == [] and pub.db.all(Message) == []
    assert events[-1].data["message_id"] is None


async def test_no_router_and_no_tools(pub: CobClient) -> None:
    pub.world.router_llm = MagicMock()
    events = await sse(pub, {"message": "how many questions have I used?"})
    assert pub.world.router_llm.mock_calls == []
    assert pub.world.internal.calls == []
    assert events[0].data["intent"] == "docs"


async def test_history_is_masked_and_not_stored(pub: CobClient) -> None:
    await sse(
        pub,
        {
            "message": "and pricing?",
            "history": [
                {"role": "user", "content": "my PAN is ABCPE1234F"},
                {"role": "assistant", "content": "ok"},
            ],
        },
    )
    history = pub.director.turn.history
    assert [m.role for m in history] == ["user", "assistant"]
    assert "ABCPE1234F" not in json.dumps([m.content for m in history])
    assert pub.db.all(Conversation) == [] and pub.db.all(Message) == []


async def test_history_is_capped(pub: CobClient) -> None:
    too_many = [{"role": "user", "content": "q"}] * 7
    assert (await post(pub, {"message": "hi", "history": too_many})).status_code == 422
    too_long = [{"role": "user", "content": "x" * 2001}]
    assert (await post(pub, {"message": "hi", "history": too_long})).status_code == 422


@pytest.mark.parametrize(
    ("env", "setting"),
    [({"cob_public_enabled": False}, True), ({}, False), ({"cob_enabled": False}, True)],
)
async def test_switches(pub: CobClient, env: dict[str, Any], setting: bool) -> None:
    pub.app.state.settings = pub.app.state.settings.model_copy(update=env)
    if not setting:
        pub.db.add(EngineSettingsRow(id=1, value={"cob_public_sign_in": False}))
    res = await post(pub, {"message": "hi"})
    assert res.status_code == 404 and res.json()["code"] == "cob_public_disabled"
    assert pub.director.built == 0


async def test_rate_limited_has_retry_after(pub: CobClient) -> None:
    pub.app.state.cob_public_limiter = PublicCobLimiter(FakeRedis(), per_ip_per_hour=1, daily=500)
    assert (await post(pub, {"message": "hi"})).status_code == 200
    res = await post(pub, {"message": "hi"})
    body = res.json()
    assert res.status_code == 429 and body["code"] == "cob_public_rate_limited"
    assert body["scope"] == "ip" and int(res.headers["Retry-After"]) > 0
    assert pub.director.built == 1


async def test_redis_down_is_503(pub: CobClient) -> None:
    redis = FakeRedis()
    redis.down = True
    pub.app.state.cob_public_limiter = PublicCobLimiter(redis, per_ip_per_hour=5, daily=5)
    res = await post(pub, {"message": "hi"})
    assert res.status_code == 503 and res.json()["code"] == "cob_public_unavailable"
    assert pub.director.built == 0


async def test_every_accepted_message_counts_even_a_free_one(pub: CobClient) -> None:
    redis = FakeRedis()
    pub.app.state.cob_public_limiter = PublicCobLimiter(redis, per_ip_per_hour=5, daily=5)
    pub.director.script = "smalltalk"
    pub.director.model_calls = 0
    await sse(pub, {"message": "thanks"})
    day = next(v for k, v in redis.values.items() if k.startswith("cobpub:day:"))
    assert day == 1


async def test_customer_topic_gets_a_sign_in_hint(pub: CobClient) -> None:
    pub.director.script = "no_answer"
    events = await sse(pub, {"message": "How do I rotate my widget key?"})
    text = tokens(events)
    assert "Sign in" in text and "Which screen are you on?" not in text
    assert not [e for e in events if e.event == "quick_replies"]
    assert events[-1].data["route"] == "restricted"
    probe = pub.retriever.calls[-1]
    assert probe["metadata_filters"] == {"audience": ["customer", "admin"]}


async def test_a_genuine_no_answer_is_left_alone(pub: CobClient) -> None:
    pub.director.script = "no_answer"
    pub.world.retriever.records = []  # nothing in the customer or admin docs either
    events = await sse(pub, {"message": "What is the weather?"})
    assert "Sign in" not in tokens(events) and "Which screen are you on?" in tokens(events)


async def test_a_session_or_key_is_ignored_not_elevated(pub: CobClient) -> None:
    for key in ("alice", "sa", "admin"):
        await sse(pub, {"message": "hi"}, key=key)
        assert pub.director.kwargs["retrieval_filters"] == {"audience": ["public"]}
        assert pub.director.turn.tenant_id == pub.world.system_tenant
    assert pub.db.all(Conversation) == []


def _ui(events: list[Any]) -> list[Any]:
    return [e for e in events if e.event == "ui"]


async def test_a_public_answer_links_only_to_a_public_screen(pub: CobClient) -> None:
    pub.director.cited = ["system:cob/screens/login#", "system:cob/readme/why-cob-rag-engine#"]
    events = await sse(pub, {"message": "How do I sign in?"})
    links = _ui(events)
    assert len(links) == 1 and links[0].data["kind"] == "cob_link"
    assert links[0].data["route"] == "/login" and not links[0].data.get("docs_slug")


async def test_a_routeless_public_page_is_never_a_link(pub: CobClient) -> None:
    pub.director.cited = ["system:cob/readme/why-cob-rag-engine#"]
    events = await sse(pub, {"message": "Why cob-rag-engine?"})
    assert _ui(events) == []


async def test_a_customer_source_is_withheld_from_a_visitor(pub: CobClient) -> None:
    events = await sse(pub, {"message": "What is cob-rag-engine?"})  # cites screens/widget
    assert not [e for e in events if e.event == "sources"]
    assert _ui(events) == []


async def test_search_mode_without_a_model_key_stores_nothing(pub: CobClient) -> None:
    pub.world.llm_ok = False
    events = await sse(pub, {"message": "How do I sign up?"})
    assert events[0].data["mode"] == "search"
    assert pub.db.all(Conversation) == [] and pub.db.all(Message) == []
    assert pub.director.built == 0


async def test_the_docs_are_not_synced_yet(pub: CobClient) -> None:
    pub.world.status.document_count = 0
    events = await sse(pub, {"message": "hi"})
    assert [e.event for e in events] == ["error"]
    assert events[0].data["code"] == "cob_docs_syncing"


async def test_strict_body(pub: CobClient) -> None:
    assert (await post(pub, {"message": "hi", "role": "admin"})).status_code == 422
    assert (await post(pub, {"message": "hi", "audience": ["admin"]})).status_code == 422
    assert (await post(pub, {"message": "x" * 1001})).status_code == 422
    assert (await post(pub, {"message": ""})).status_code == 422
    bad = {"message": "hi", "history": [{"role": "system", "content": "x"}]}
    assert (await post(pub, bad)).status_code == 422


async def test_the_429_is_readable_by_a_browser(pub: CobClient) -> None:
    pub.app.state.cob_public_limiter = PublicCobLimiter(FakeRedis(), per_ip_per_hour=0, daily=500)
    origin = "http://127.0.0.1:8090"
    async with AsyncClient(transport=ASGITransport(app=pub.app), base_url="http://t") as client:
        res = await client.post(PUBLIC, json={"message": "hi"}, headers={"Origin": origin})
    assert res.status_code == 429 and int(res.headers["Retry-After"]) > 0
    exposed = res.headers["access-control-expose-headers"].lower()
    assert "retry-after" in exposed


async def test_ipv6_addresses_in_one_64_share_a_bucket() -> None:
    from datetime import UTC, datetime

    limiter = PublicCobLimiter(FakeRedis(), per_ip_per_hour=1, daily=500)
    now = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
    assert (await limiter.hit("2001:db8:1:2::1", now)).allowed
    second = await limiter.hit("2001:db8:1:2:ffff:ffff:ffff:ffff", now)
    assert not second.allowed and second.scope == "ip"
    assert (await limiter.hit("2001:db8:1:3::1", now)).allowed  # another /64
    assert (await limiter.hit("203.0.113.9", now)).allowed  # v4 untouched


async def test_a_limiter_hit_sets_the_expiry_with_the_count() -> None:
    from datetime import UTC, datetime

    redis = FakeRedis()
    limiter = PublicCobLimiter(redis, per_ip_per_hour=5, daily=5)
    await limiter.hit("203.0.113.9", datetime(2026, 10, 4, 12, 0, tzinfo=UTC))
    assert redis.values and set(redis.values) == set(redis.expiry)
