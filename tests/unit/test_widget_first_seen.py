"""Where a widget loads from is remembered (BIZ4): the install light of the wizard.

The route wiring is tested with a mocked session (like test_widget.py); the upsert and the
refused-origin cap are checked as the SQL they send, and their behaviour on PostgreSQL is in
`tests/integration/test_onboarding_db.py`.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import postgresql

from callobuzz_rag.api.app import create_app
from callobuzz_rag.core.config import Settings
from callobuzz_rag.onboarding import service as onboarding
from callobuzz_rag.widget.security import LimitResult

pytestmark = pytest.mark.usefixtures("unlimited_plan")

SITE = "https://shreedental.in"
KEY = "pk_" + "a" * 32


def _key(corpus_id: uuid.UUID, origins: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        corpus_id=corpus_id,
        name="Website",
        public_key=KEY,
        allowed_origins=origins if origins is not None else [SITE],
        rate_limit_per_minute=10,
        daily_message_limit=1000,
        appearance={},
        is_active=True,
    )


@pytest.fixture
def corpus() -> SimpleNamespace:
    cid = uuid.uuid4()
    return SimpleNamespace(
        id=cid,
        tenant_id=uuid.uuid4(),
        qdrant_collection=f"corpus_{str(cid)[:8]}",
        retrieval_config=None,
        llm_config=None,
        prompt_config=None,
        is_system=False,
    )


@pytest.fixture
def app():
    application = create_app()
    application.state.settings = Settings(
        _env_file=None, admin_api_key="test-admin-key", llm_api_key="test"
    )
    application.state.widget_rate_limiter = SimpleNamespace(
        check=AsyncMock(return_value=LimitResult(True)),
        _hit=AsyncMock(return_value=(True, 0)),
    )
    application.state.own_session = _own_session()
    application.state.session_factory = lambda: application.state.own_session
    return application


def _own_session() -> MagicMock:
    """The session the bookkeeping opens for itself: an async context manager."""
    own = MagicMock()
    own.__aenter__ = AsyncMock(return_value=own)
    own.__aexit__ = AsyncMock(return_value=False)
    own.rollback = AsyncMock()
    own.get = AsyncMock()
    return own


def _use_session(app, key: SimpleNamespace, corpus: SimpleNamespace) -> None:
    from callobuzz_rag.api.deps import get_session

    session = AsyncMock()
    app.state.request_session = session
    result = MagicMock()
    result.scalar_one_or_none.return_value = key
    session.execute = AsyncMock(return_value=result)
    tenant = SimpleNamespace(id=corpus.tenant_id, status="active")
    session.get = AsyncMock(
        side_effect=lambda model, _id: tenant if model.__name__ == "Tenant" else corpus
    )

    async def override():
        yield session

    app.dependency_overrides[get_session] = override


@pytest.fixture
def spies(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    seen = AsyncMock(return_value=True)
    learn = AsyncMock()
    monkeypatch.setattr(onboarding, "record_sighting", seen)
    monkeypatch.setattr(onboarding.website_service, "start_learning", learn)
    monkeypatch.setattr(onboarding.website_service, "sources_of", AsyncMock(return_value=[]))
    return SimpleNamespace(seen=seen, learn=learn)


async def _get(app, origin: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.get("/api/v1/widget/config", headers={"X-Widget-Key": KEY, "Origin": origin})


async def test_an_allowed_load_is_recorded_as_allowed(app, corpus, spies) -> None:
    _use_session(app, _key(corpus.id), corpus)
    assert (await _get(app, SITE)).status_code == 200
    spies.seen.assert_awaited_once()
    assert spies.seen.await_args.kwargs["origin"] == SITE
    assert spies.seen.await_args.kwargs["allowed"] is True


async def test_a_refused_origin_is_remembered_but_still_refused(app, corpus, spies) -> None:
    _use_session(app, _key(corpus.id), corpus)  # only the apex is allowed
    res = await _get(app, "https://www.shreedental.in")
    assert res.status_code == 403
    spies.seen.assert_awaited_once()
    assert spies.seen.await_args.kwargs["origin"] == "https://www.shreedental.in"
    assert spies.seen.await_args.kwargs["allowed"] is False
    spies.learn.assert_not_awaited()


async def test_a_missing_origin_records_nothing(app, corpus, spies) -> None:
    _use_session(app, _key(corpus.id), corpus)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        res = await c.get("/api/v1/widget/config", headers={"X-Widget-Key": KEY})
    assert res.status_code == 403
    spies.seen.assert_not_awaited()


async def test_first_allowed_sighting_starts_learning_when_the_site_has_none(
    app, corpus, spies
) -> None:
    _use_session(app, _key(corpus.id), corpus)
    assert (await _get(app, SITE)).status_code == 200
    spies.learn.assert_awaited_once()
    assert spies.learn.await_args.args[2] == SITE
    assert spies.learn.await_args.kwargs["trigger"] == "widget"
    assert spies.learn.await_args.kwargs["request"] is None


async def test_a_repeat_sighting_never_starts_learning(app, corpus, spies) -> None:
    spies.seen.return_value = False
    _use_session(app, _key(corpus.id), corpus)
    assert (await _get(app, SITE)).status_code == 200
    spies.learn.assert_not_awaited()


async def test_a_site_that_already_has_a_source_is_not_started_again(
    app, corpus, spies, monkeypatch
) -> None:
    monkeypatch.setattr(
        onboarding.website_service,
        "sources_of",
        AsyncMock(return_value=[SimpleNamespace(scope="shreedental.in")]),
    )
    _use_session(app, _key(corpus.id), corpus)
    assert (await _get(app, SITE)).status_code == 200
    spies.learn.assert_not_awaited()


async def test_wildcard_keys_record_and_learn_nothing(app, corpus, spies) -> None:
    _use_session(app, _key(corpus.id, ["*"]), corpus)
    assert (await _get(app, "https://random.example")).status_code == 200
    spies.seen.assert_not_awaited()  # a `*` match is no evidence of the business's site
    spies.learn.assert_not_awaited()


async def test_a_key_with_a_wildcard_and_the_site_still_needs_the_literal_origin(
    app, corpus, spies
) -> None:
    _use_session(app, _key(corpus.id, ["*", SITE]), corpus)
    assert (await _get(app, "https://random.example")).status_code == 200
    spies.seen.assert_not_awaited()
    spies.learn.assert_not_awaited()
    assert (await _get(app, SITE)).status_code == 200
    spies.learn.assert_awaited_once()


async def test_a_database_error_never_breaks_the_widget(app, corpus, spies) -> None:
    spies.seen.side_effect = RuntimeError("database is down")
    _use_session(app, _key(corpus.id), corpus)
    assert (await _get(app, SITE)).status_code == 200


async def test_a_database_error_never_changes_a_refusal(app, corpus, spies) -> None:
    spies.seen.side_effect = RuntimeError("database is down")
    _use_session(app, _key(corpus.id), corpus)
    assert (await _get(app, "https://evil.example")).status_code == 403


async def test_a_learning_error_never_breaks_the_widget(app, corpus, spies) -> None:
    spies.learn.side_effect = RuntimeError("queue is down")
    _use_session(app, _key(corpus.id), corpus)
    assert (await _get(app, SITE)).status_code == 200


# ---------------------------------------------------------------- the SQL


def test_the_upsert_writes_at_most_once_an_hour() -> None:
    sql = str(
        onboarding.sighting_statement(uuid.uuid4(), uuid.uuid4(), SITE, True).compile(
            dialect=postgresql.dialect()
        )
    )
    assert "ON CONFLICT (widget_key_id, origin) DO UPDATE" in sql
    assert "last_seen_at < " in sql and "interval" in sql.lower()
    assert "RETURNING" in sql and "xmax = 0" in sql


async def test_at_most_twenty_refused_origins_per_key_the_oldest_is_replaced() -> None:
    key_id, corpus_id = uuid.uuid4(), uuid.uuid4()
    refused = [
        SimpleNamespace(origin=f"https://site{i}.example", last_seen_at=i) for i in range(20)
    ]  # oldest first
    session = AsyncMock()
    listing = MagicMock()
    listing.scalars.return_value.all.return_value = refused
    upsert = MagicMock()
    upsert.first.return_value = SimpleNamespace(inserted=True)
    session.execute = AsyncMock(side_effect=[listing, MagicMock(), upsert])
    first = await onboarding.record_sighting(
        session, key_id=key_id, corpus_id=corpus_id, origin="https://new.example", allowed=False
    )
    assert first is True
    delete_sql = str(
        session.execute.await_args_list[1].args[0].compile(dialect=postgresql.dialect())
    )
    assert delete_sql.startswith("DELETE FROM widget_origin_seen")
    session.commit.assert_awaited()


# ---------------------------------------------------------------- hardening


async def test_an_unrelated_refused_site_is_never_written_down(app, corpus, spies) -> None:
    _use_session(app, _key(corpus.id), corpus)
    assert (await _get(app, "https://evil.example")).status_code == 403
    spies.seen.assert_not_awaited()


async def test_a_refused_twin_is_written_down_once_an_hour(app, corpus, spies) -> None:
    app.state.widget_rate_limiter._hit.side_effect = [(True, 0), (True, 0), (False, 3000)]
    _use_session(app, _key(corpus.id), corpus)
    assert (await _get(app, "https://www.shreedental.in")).status_code == 403
    assert (await _get(app, "https://www.shreedental.in")).status_code == 403
    assert spies.seen.await_count == 1  # the second was over the per-site budget


async def test_with_redis_down_nothing_is_written_and_the_refusal_stands(
    app, corpus, spies
) -> None:
    app.state.widget_rate_limiter._hit.side_effect = RuntimeError("redis is down")
    _use_session(app, _key(corpus.id), corpus)
    assert (await _get(app, "https://www.shreedental.in")).status_code == 403
    spies.seen.assert_not_awaited()


async def test_an_overlong_origin_is_never_written(app, corpus, spies) -> None:
    _use_session(app, _key(corpus.id, ["*", SITE]), corpus)
    assert (await _get(app, "https://" + "a" * 300 + ".example")).status_code == 200
    spies.seen.assert_not_awaited()


async def test_the_bookkeeping_never_touches_the_request_session(app, corpus, spies) -> None:
    spies.seen.side_effect = RuntimeError("value too long")
    _use_session(app, _key(corpus.id), corpus)
    assert (await _get(app, SITE)).status_code == 200
    app.state.request_session.rollback.assert_not_awaited()
    app.state.own_session.rollback.assert_awaited()  # only its own session is rolled back
    assert spies.seen.await_args.args[0] is app.state.own_session


async def test_learning_runs_in_the_bookkeeping_session_and_a_failure_rolls_it_back(
    app, corpus, spies
) -> None:
    app.state.own_session.get.return_value = corpus
    spies.learn.side_effect = RuntimeError("flush failed")
    _use_session(app, _key(corpus.id), corpus)
    assert (await _get(app, SITE)).status_code == 200
    assert spies.learn.await_args.args[0] is app.state.own_session
    app.state.own_session.rollback.assert_awaited()
    app.state.request_session.rollback.assert_not_awaited()


def test_twins_are_the_same_site_in_another_spelling() -> None:
    allowed = {"https://shreedental.in"}
    assert onboarding._is_twin("https://www.shreedental.in", allowed)
    assert onboarding._is_twin("http://shreedental.in", allowed)
    assert not onboarding._is_twin("https://shreedental.in", allowed)  # already named
    assert not onboarding._is_twin("https://evil.example", allowed)
    assert not onboarding._is_twin("https://shreedental.in.evil.example", allowed)


def test_a_refused_row_that_becomes_allowed_is_a_first_load_and_not_held_back_by_the_hour() -> None:
    sql = str(
        onboarding.sighting_statement(uuid.uuid4(), uuid.uuid4(), SITE, True).compile(
            dialect=postgresql.dialect()
        )
    )
    assert "IS DISTINCT FROM" in sql  # the flip bypasses the hour
    assert "first_seen_at = CASE WHEN" in sql.replace(chr(10), " ")
    assert "first_seen_at = last_seen_at" in sql
